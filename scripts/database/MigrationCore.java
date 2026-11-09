import java.io.*;
import java.math.BigDecimal;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.nio.file.attribute.*;
import java.security.MessageDigest;
import java.sql.*;
import java.time.LocalDateTime;
import java.util.*;
import tools.jackson.core.StreamReadFeature;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/** 仅做严格类型转换与有界指纹；不记录值，不生成 DDL。 */
final class MigrationCore {
    static final JsonMapper JSON = JsonMapper.builder().enable(StreamReadFeature.STRICT_DUPLICATE_DETECTION)
            .enable(tools.jackson.databind.cfg.JsonNodeFeature.USE_BIG_DECIMAL_FOR_FLOATS).build();
    static final Set<String> DATABASES = Set.of("github_dev_ai", "github_dev_ai_memory", "github_dev_mcp",
            "github_dev_message", "github_dev_rag", "github_dev_report", "github_dev_user");
    enum Kind { STRING, INTEGER, BOOLEAN, DECIMAL, FLOAT, TIMESTAMP, JSON, BINARY }
    record Column(String name, Kind kind, boolean nullable, boolean identity) {}

    static final class Failure extends RuntimeException {
        final String reason; final String sqlState; final Integer vendorCode;
        Failure(String reason) { super(reason, null, false, false); this.reason=reason; sqlState=null; vendorCode=null; }
        Failure(String reason, SQLException error) {
            super(reason, null, false, false); this.reason=reason;
            String state=error.getSQLState(); sqlState=state != null && state.matches("[0-9A-Z]{5}") ? state : null;
            vendorCode=error.getErrorCode();
        }
        Map<String,Object> report() {
            Map<String,Object> result=new LinkedHashMap<>(); result.put("status","rejected"); result.put("reason",reason);
            if (sqlState!=null) result.put("sql_state",sqlState); if (vendorCode!=null) result.put("vendor_code",vendorCode);
            return result;
        }
    }
    static void require(boolean condition, String reason) { if (!condition) throw new Failure(reason); }
    static String identifier(String value) { require(value != null && value.matches("[a-z][a-z0-9_]{0,62}"),"IDENTIFIER_REJECTED"); return value; }
    static String database(String value) { require(DATABASES.contains(value),"DATABASE_NOT_ALLOWED"); return value; }
    static String pg(String value) { return "\""+identifier(value)+"\""; }
    static String mysql(String value) { return "`"+identifier(value)+"`"; }
    static byte[] sha(byte[] value) { try { return MessageDigest.getInstance("SHA-256").digest(value); } catch (Exception e) { throw new Failure("HASH_UNAVAILABLE"); } }
    static String hex(byte[] value) { return HexFormat.of().formatHex(value); }
    static String fingerprint(Object value) { return hex(sha(canonicalJson(JSON.writeValueAsString(value),16*1024*1024).getBytes(StandardCharsets.UTF_8))); }
    static boolean sameJson(Object first,Object second) { return first==null||second==null ? first==second : fingerprint(first).equals(fingerprint(second)); }
    static byte[] utf8(String text) {
        try { var encoder=StandardCharsets.UTF_8.newEncoder().onMalformedInput(java.nio.charset.CodingErrorAction.REPORT).onUnmappableCharacter(java.nio.charset.CodingErrorAction.REPORT);var buffer=encoder.encode(java.nio.CharBuffer.wrap(text));byte[] bytes=new byte[buffer.remaining()];buffer.get(bytes);return bytes; }
        catch(java.nio.charset.CharacterCodingException e){throw new Failure("TEXT_ENCODING_INVALID");}
    }

    static byte[] privateBytes(Path path, int limit) {
        try {
            require(path.isAbsolute(),"PRIVATE_FILE_PATH");
            var before=Files.readAttributes(path,BasicFileAttributes.class,LinkOption.NOFOLLOW_LINKS);
            require(before.isRegularFile() && !before.isSymbolicLink(),"PRIVATE_FILE_TYPE");
            require(Files.getPosixFilePermissions(path,LinkOption.NOFOLLOW_LINKS).equals(PosixFilePermissions.fromString("rw-------")),"PRIVATE_FILE_PERMISSION");
            require(Files.getOwner(path,LinkOption.NOFOLLOW_LINKS).getName().equals(System.getProperty("user.name")),"PRIVATE_FILE_OWNER");
            for(Path p=path.toRealPath().getParent();p!=null;p=p.getParent()) require(!Files.exists(p.resolve(".git")),"PRIVATE_FILE_IN_GIT");
            byte[] bytes;
            try(var input=Files.newInputStream(path,LinkOption.NOFOLLOW_LINKS)) { bytes=input.readNBytes(limit+1); }
            require(bytes.length<=limit,"PRIVATE_FILE_TOO_LARGE");
            var after=Files.readAttributes(path,BasicFileAttributes.class,LinkOption.NOFOLLOW_LINKS);
            require(Objects.equals(before.fileKey(),after.fileKey()) && before.size()==after.size() && before.lastModifiedTime().equals(after.lastModifiedTime()),"PRIVATE_FILE_CHANGED");
            return bytes;
        } catch (Failure e) { throw e; } catch (Exception e) { throw new Failure("PRIVATE_FILE_UNAVAILABLE"); }
    }
    static void privateDirectory(Path path) {
        try {
            require(path.isAbsolute() && Files.isDirectory(path,LinkOption.NOFOLLOW_LINKS) && !Files.isSymbolicLink(path),"PRIVATE_DIRECTORY_TYPE");
            require(Files.getPosixFilePermissions(path).equals(PosixFilePermissions.fromString("rwx------")),"PRIVATE_DIRECTORY_PERMISSION");
            require(Files.getOwner(path).getName().equals(System.getProperty("user.name")),"PRIVATE_DIRECTORY_OWNER");
            for(Path p=path.toRealPath();p!=null;p=p.getParent()) require(!Files.exists(p.resolve(".git")),"PRIVATE_FILE_IN_GIT");
        } catch(Failure e) { throw e; } catch(Exception e) { throw new Failure("PRIVATE_DIRECTORY_UNAVAILABLE"); }
    }
    static Properties properties(Path path) {
        Properties result=new Properties() { @Override public synchronized Object put(Object key,Object value) {
            require(!containsKey(key),"DUPLICATE_PROPERTY"); return super.put(key,value);
        }};
        try { result.load(new StringReader(new String(privateBytes(path,65536),StandardCharsets.UTF_8))); }
        catch(Failure e) { throw e; } catch(Exception e) { throw new Failure("PROPERTIES_INVALID"); }
        require(result.keySet().equals(Set.of("url","user","password")),"PROPERTIES_KEYS");
        for(String key:List.of("url","user","password")) require(!result.getProperty(key).isEmpty(),"PROPERTIES_EMPTY");
        return result;
    }

    static Object normalize(Kind kind,Object value,int limit) {
        if(value==null) return null;
        try {
            Object normalized=switch(kind) {
                case STRING -> { require(value instanceof String,"STRING_TYPE"); String text=(String)value; require(text.indexOf('\0')<0,"TEXT_NUL_UNSUPPORTED"); yield text; }
                case INTEGER -> { require(value instanceof BigDecimal||value instanceof java.math.BigInteger||value instanceof Byte||value instanceof Short||value instanceof Integer||value instanceof Long,"INTEGER_TYPE");BigDecimal number=value instanceof BigDecimal b ? b : new BigDecimal(value.toString()); yield number.longValueExact(); }
                case BOOLEAN -> {
                    if(value instanceof Boolean) yield value;
                    if(value instanceof byte[] bytes) { require(bytes.length==1 && (bytes[0]==0 || bytes[0]==1),"BOOLEAN_VALUE"); yield bytes[0]==1; }
                    require(value instanceof Number,"BOOLEAN_TYPE"); BigDecimal number=new BigDecimal(value.toString());
                    require(number.compareTo(BigDecimal.ZERO)==0 || number.compareTo(BigDecimal.ONE)==0,"BOOLEAN_VALUE"); yield number.compareTo(BigDecimal.ONE)==0;
                }
                case DECIMAL -> { require(value instanceof BigDecimal,"DECIMAL_TYPE"); BigDecimal number=(BigDecimal)value; require(Math.abs((long)number.scale())<=10000,"DECIMAL_RANGE"); yield number.signum()==0 ? BigDecimal.ZERO : number.stripTrailingZeros(); }
                case FLOAT -> { require(value instanceof Number,"FLOAT_TYPE"); double number=((Number)value).doubleValue(); require(Double.isFinite(number),"FLOAT_NONFINITE"); yield number==0d ? 0d : number; }
                case TIMESTAMP -> { require(value instanceof LocalDateTime,"TIMESTAMP_TYPE"); LocalDateTime time=(LocalDateTime)value; require(time.getNano()%1000==0,"TIMESTAMP_PRECISION"); yield time; }
                case JSON -> { require(value instanceof String,"JSON_TYPE"); yield canonicalJson((String)value,limit); }
                case BINARY -> { require(value instanceof byte[],"BINARY_TYPE"); yield ((byte[])value).clone(); }
            };
            require(bytes(kind,normalized).length<=limit,"ROW_BYTES_LIMIT"); return normalized;
        } catch(Failure e) { throw e; } catch(RuntimeException e) { throw new Failure("VALUE_CONVERSION_FAILED"); }
    }
    static byte[] bytes(Kind kind,Object value) {
        if(value==null) return new byte[0];
        String text=switch(kind) {
            case STRING,JSON -> (String)value;
            case INTEGER -> Long.toString((Long)value);
            case BOOLEAN -> (Boolean)value ? "1" : "0";
            case DECIMAL -> ((BigDecimal)value).toPlainString();
            case FLOAT -> Double.toHexString((Double)value);
            case TIMESTAMP -> ((LocalDateTime)value).toString();
            case BINARY -> null;
        };
        return kind==Kind.BINARY ? (byte[])value : utf8(text);
    }
    static String canonicalJson(String text,int limit) {
        require(utf8(text).length<=limit,"ROW_BYTES_LIMIT");
        try { JsonNode node=JSON.readTree(text); require(node!=null,"JSON_INVALID"); StringBuilder out=new StringBuilder(); json(node,out,0,limit); return out.toString(); }
        catch(Failure e) { throw e; } catch(RuntimeException e) { throw new Failure("JSON_INVALID"); }
    }
    private static void json(JsonNode value,StringBuilder out,int depth,int limit) {
        require(depth<=64,"JSON_DEPTH_LIMIT");
        if(value.isObject()) {
            TreeMap<String,JsonNode> sorted=new TreeMap<>(); value.properties().forEach(p->sorted.put(p.getKey(),p.getValue()));
            out.append('{'); boolean first=true;
            for(var entry:sorted.entrySet()) { require(entry.getKey().indexOf('\0')<0,"JSON_NUL_UNSUPPORTED");utf8(entry.getKey());if(!first) out.append(','); first=false; out.append(JSON.writeValueAsString(entry.getKey())).append(':'); json(entry.getValue(),out,depth+1,limit); }
            out.append('}');
        } else if(value.isArray()) { out.append('['); boolean first=true; for(JsonNode item:value) { if(!first) out.append(','); first=false; json(item,out,depth+1,limit); } out.append(']'); }
        else if(value.isNumber()) { BigDecimal number=value.decimalValue(); require(Math.abs((long)number.scale())<=10000,"JSON_NUMBER_RANGE"); out.append(number.signum()==0 ? "0" : number.stripTrailingZeros().toPlainString()); }
        else if(value.isString()) { require(value.asString().indexOf('\0')<0,"JSON_NUL_UNSUPPORTED");utf8(value.asString());out.append(JSON.writeValueAsString(value.asString())); }
        else if(value.isBoolean() || value.isNull()) out.append(value.toString());
        else throw new Failure("JSON_TYPE");
        require(out.length()<=limit,"ROW_BYTES_LIMIT");
    }
    static Object read(ResultSet rows,int index,Column column,int remaining) throws SQLException {
        Object value;
        if(column.kind==Kind.BINARY) {
            try(InputStream input=rows.getBinaryStream(index)) { value=input==null?null:input.readNBytes(remaining+1); }
            catch(IOException e) { throw new Failure("BINARY_READ_FAILED"); }
        } else if(column.kind==Kind.STRING || column.kind==Kind.JSON) {
            try(Reader input=rows.getCharacterStream(index)) {
                if(input==null) value=null;
                else { StringBuilder text=new StringBuilder(); char[] buffer=new char[4096]; int n;
                    while((n=input.read(buffer,0,Math.min(buffer.length,remaining+1-text.length())))!=-1) {
                        text.append(buffer,0,n); require(text.length()<=remaining,"ROW_BYTES_LIMIT");
                    } value=text.toString(); }
            } catch(IOException e) { throw new Failure("TEXT_READ_FAILED"); }
        } else if(column.kind==Kind.TIMESTAMP) value=rows.getObject(index,LocalDateTime.class);
        else if(column.kind==Kind.INTEGER || column.kind==Kind.DECIMAL) value=rows.getBigDecimal(index);
        else if(column.kind==Kind.FLOAT) { double number=rows.getDouble(index); value=rows.wasNull()?null:number; }
        else value=rows.getObject(index);
        require(value!=null || column.nullable,"NULL_NOT_ALLOWED"); return normalize(column.kind,value,remaining);
    }
    static void bind(PreparedStatement statement,int index,Column column,Object value) throws SQLException {
        if(value==null) { statement.setNull(index,switch(column.kind) { case BINARY->Types.BINARY;case JSON->Types.OTHER;case BOOLEAN->Types.BOOLEAN;case INTEGER->Types.BIGINT;case DECIMAL->Types.NUMERIC;case FLOAT->Types.DOUBLE;case TIMESTAMP->Types.TIMESTAMP;case STRING->Types.VARCHAR; }); return; }
        if(column.kind==Kind.JSON) { var json=new org.postgresql.util.PGobject(); json.setType("jsonb"); json.setValue((String)value); statement.setObject(index,json); }
        else if(column.kind==Kind.BINARY) statement.setBytes(index,(byte[])value);
        else statement.setObject(index,value);
    }
    static final class Rows {
        final long maxRows; final int maxRowBytes; final List<byte[]> hashes=new ArrayList<>();
        Rows(long maxRows,int maxRowBytes) { this.maxRows=maxRows;this.maxRowBytes=maxRowBytes; }
        void add(List<Column> columns,List<Object> values) {
            require(hashes.size()<maxRows,"ROW_COUNT_LIMIT");
            try { MessageDigest digest=MessageDigest.getInstance("SHA-256"); int remaining=maxRowBytes;
                for(int i=0;i<columns.size();i++) {
                    Object value=values.get(i); byte[] bytes=bytes(columns.get(i).kind,value); remaining-=bytes.length;
                    require(remaining>=0,"ROW_BYTES_LIMIT"); digest.update((byte)columns.get(i).kind.ordinal()); digest.update((byte)(value==null?0:1));
                    digest.update(ByteBuffer.allocate(4).putInt(bytes.length).array()); digest.update(bytes);
                } hashes.add(digest.digest());
            } catch(Failure e) { throw e; } catch(Exception e) { throw new Failure("HASH_UNAVAILABLE"); }
        }
        long count() { return hashes.size(); }
        String finish() {
            hashes.sort(Arrays::compareUnsigned);
            try { MessageDigest digest=MessageDigest.getInstance("SHA-256"); digest.update(ByteBuffer.allocate(8).putLong(hashes.size()).array()); for(byte[] h:hashes) digest.update(h); return hex(digest.digest()); }
            catch(Exception e) { throw new Failure("HASH_UNAVAILABLE"); }
        }
    }
    @FunctionalInterface interface Work<T> { T run() throws SQLException; }
    static <T> T transaction(Connection connection,Work<T> work) throws SQLException {
        connection.setReadOnly(false); connection.setAutoCommit(false);
        try { T result=work.run(); connection.commit(); return result; }
        catch(SQLException | RuntimeException | Error error) { try { connection.rollback(); } catch(SQLException ignored) {} throw error; }
    }
}
