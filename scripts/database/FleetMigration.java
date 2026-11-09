import java.io.*;
import java.net.URI;
import java.nio.channels.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.nio.file.attribute.PosixFilePermissions;
import java.sql.*;
import java.util.*;
import tools.jackson.databind.JsonNode;

/** 个人七库的一次性受控复制器：源只读快照；目标只 INSERT，不建表、不覆盖。 */
public final class FleetMigration {
    static final List<String> DATABASES=MigrationCore.DATABASES.stream().sorted().toList();
    static final List<String> COLUMN_KEYS=List.of("TABLE_SCHEMA","TABLE_NAME","COLUMN_NAME","ORDINAL_POSITION","DATA_TYPE","COLUMN_TYPE","IS_NULLABLE","EXTRA","CHARACTER_MAXIMUM_LENGTH","NUMERIC_PRECISION","NUMERIC_SCALE","DATETIME_PRECISION","CHARACTER_SET_NAME","COLLATION_NAME","COLUMN_DEFAULT","GENERATION_EXPRESSION","COLUMN_KEY");
    static final List<String> INDEX_KEYS=List.of("TABLE_SCHEMA","TABLE_NAME","INDEX_NAME","NON_UNIQUE","INDEX_TYPE","SEQ_IN_INDEX","COLUMN_NAME","COLLATION","SUB_PART","EXPRESSION");
    static final List<String> TABLE_KEYS=List.of("TABLE_SCHEMA","TABLE_NAME","TABLE_TYPE","ENGINE");
    record Config(Path source,Map<String,Path> targets,Path metadata,String metadataSha,Path receipts,long maxRows,int maxRowBytes,int timeout,String window) {}
    record Table(String database,String name,List<MigrationCore.Column> columns,List<Map<String,String>> sourceColumns,Long sourceNextId) {
        Table(String database,String name,List<MigrationCore.Column> columns,List<Map<String,String>> sourceColumns){this(database,name,columns,sourceColumns,null);}
    }
    record SequenceSetting(long value,boolean called) {}
    record Stat(long count,String sha256) { Map<String,Object> json() { return Map.of("count",count,"sha256",sha256); } }
    record Shape(String sha256,Set<String> identityColumns,Map<String,Map<String,String>> columns) {}
    record Prepared(Config config,Connection source,List<Table> tables,String sourceShape,String sourceAuthority,
                    Map<String,Connection> targets,Map<String,Shape> shapes,Map<String,Map<String,Stat>> sourceStats,String planSha) implements AutoCloseable {
        public void close() { for(Connection c:targets.values()) try { c.close(); } catch(SQLException ignored) {} try { source.rollback(); source.close(); } catch(SQLException ignored) {} }
    }
    static void check(boolean condition,String code) { MigrationCore.require(condition,code); }
    static String text(JsonNode node,String key) { JsonNode value=node.get(key); check(value!=null&&value.isString(),"CONFIG_VALUE"); return value.asString(); }
    static Path path(JsonNode node,String key) { return Path.of(text(node,key)); }
    static void exact(JsonNode node,Set<String> keys,String reason) { check(node!=null&&node.isObject()&&node.properties().stream().map(Map.Entry::getKey).collect(java.util.stream.Collectors.toSet()).equals(keys),reason); }
    static Config config(Path file) {
        JsonNode value;
        try { value=MigrationCore.JSON.readTree(MigrationCore.privateBytes(file,131072)); }
        catch(MigrationCore.Failure e) { throw e; } catch(RuntimeException e) { throw new MigrationCore.Failure("CONFIG_INVALID"); }
        exact(value,Set.of("schema_version","source_properties_ref","target_properties_refs","metadata_ref","metadata_sha256","receipt_dir","max_rows_per_table","max_row_bytes","query_timeout_seconds","write_window_id"),"CONFIG_KEYS");
        check(value.path("schema_version").isInt()&&value.path("schema_version").intValue()==1,"CONFIG_VERSION");
        JsonNode target=value.get("target_properties_refs"); exact(target,MigrationCore.DATABASES,"TARGET_DATABASE_SET");
        Map<String,Path> targets=new TreeMap<>(); for(String db:DATABASES) targets.put(db,path(target,db));
        String hash=text(value,"metadata_sha256"); check(hash.matches("[0-9a-f]{64}"),"METADATA_SHA_INVALID");
        long maxRows=integer(value,"max_rows_per_table",1,1_000_000); int maxBytes=(int)integer(value,"max_row_bytes",1024,16*1024*1024);
        int timeout=(int)integer(value,"query_timeout_seconds",1,600); String window=text(value,"write_window_id");
        check(window.matches("[A-Za-z0-9_-]{8,64}"),"WRITE_WINDOW_ID_INVALID");
        Path receipts=path(value,"receipt_dir"); MigrationCore.privateDirectory(receipts);
        return new Config(path(value,"source_properties_ref"),targets,path(value,"metadata_ref"),hash,receipts,maxRows,maxBytes,timeout,window);
    }
    static long integer(JsonNode value,String key,long minimum,long maximum) {
        JsonNode number=value.get(key); check(number!=null&&number.isIntegralNumber()&&number.canConvertToLong(),"CONFIG_INTEGER");
        long result=number.longValue(); check(result>=minimum&&result<=maximum,"CONFIG_RANGE"); return result;
    }
    static String url(Properties properties,boolean source,String database) {
        String value=properties.getProperty("url"),prefix=source?"jdbc:mysql://":"jdbc:postgresql://";
        check(value.startsWith(prefix),"JDBC_URL_SCHEME");
        try {
            URI parsed=URI.create(value.substring(5));
            check(parsed.getUserInfo()==null&&parsed.getFragment()==null&&parsed.getHost()!=null,"JDBC_URL_AUTHORITY");
            check(parsed.getRawPath()!=null&&parsed.getRawPath().equals("/"+database),"JDBC_DATABASE_MISMATCH");
            Set<String> allowed=source?Set.of("sslMode","useSSL","requireSSL","verifyServerCertificate","allowPublicKeyRetrieval","connectTimeout","socketTimeout")
                    :Set.of("sslmode","ssl","connectTimeout","socketTimeout","ApplicationName");
            if(parsed.getRawQuery()!=null) {
                Set<String> seen=new HashSet<>();
                for(String part:parsed.getRawQuery().split("&",-1)) {
                    String[] pair=part.split("=",2);check(pair.length==2&&allowed.contains(pair[0])&&seen.add(pair[0]),"JDBC_URL_OPTION");
                    String key=pair[0],setting=pair[1];
                    if(key.equals("connectTimeout")||key.equals("socketTimeout")) {
                        check(setting.matches("[0-9]{1,6}"),"JDBC_TIMEOUT_RANGE");int number=Integer.parseInt(setting);
                        int maximum=key.equals("connectTimeout")?(source?10000:10):(source?600000:600);check(number>=1&&number<=maximum,"JDBC_TIMEOUT_RANGE");
                    } else if(key.equals("sslMode"))check(Set.of("DISABLED","PREFERRED","REQUIRED","VERIFY_CA","VERIFY_IDENTITY").contains(setting),"JDBC_SSL_MODE");
                    else if(key.equals("sslmode"))check(Set.of("disable","allow","prefer","require","verify-ca","verify-full").contains(setting),"JDBC_SSL_MODE");
                    else if(key.equals("ApplicationName"))check(setting.equals("personal-fleet-migration"),"JDBC_APPLICATION_NAME");
                    else check(Set.of("true","false").contains(setting),"JDBC_BOOLEAN_OPTION");
                }
            }
            return value;
        } catch(MigrationCore.Failure e) { throw e; } catch(RuntimeException e) { throw new MigrationCore.Failure("JDBC_URL_INVALID"); }
    }
    static Connection connect(Path reference,boolean source,String database,int timeout) throws SQLException {
        Properties credentials=MigrationCore.properties(reference); String url=url(credentials,source,database);
        if(!source) check(database.equals(credentials.getProperty("user")),"TARGET_ROLE_MISMATCH");
        Properties driver=new Properties(); driver.setProperty("user",credentials.getProperty("user")); driver.setProperty("password",credentials.getProperty("password"));
        if(source) {
            driver.setProperty("useCursorFetch","true"); driver.setProperty("useServerPrepStmts","true"); driver.setProperty("defaultFetchSize","256");
            driver.setProperty("tinyInt1isBit","false"); driver.setProperty("connectionTimeZone","UTC"); driver.setProperty("forceConnectionTimeZoneToSession","true");
            driver.setProperty("logger","com.mysql.cj.log.NullLogger"); driver.setProperty("connectTimeout","10000"); driver.setProperty("socketTimeout",Integer.toString(timeout*1000));
        } else { driver.setProperty("ApplicationName","personal-fleet-migration"); driver.setProperty("connectTimeout","10"); driver.setProperty("socketTimeout",Integer.toString(timeout)); }
        Connection connection=DriverManager.getConnection(url,driver);
        try {
            check(connection.getMetaData().getDatabaseProductName().equals(source?"MySQL":"PostgreSQL"),"DATABASE_PRODUCT");
            check(database.equals(connection.getCatalog()),"DATABASE_CATALOG");
            if(source) {
                connection.setTransactionIsolation(Connection.TRANSACTION_REPEATABLE_READ); connection.setReadOnly(true); connection.setAutoCommit(false);
                try(Statement statement=statement(connection,timeout)) { statement.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY"); }
                try(Statement statement=statement(connection,timeout);ResultSet rows=statement.executeQuery("SELECT @@session.transaction_read_only, @@session.transaction_isolation")) {
                    check(rows.next()&&rows.getInt(1)==1&&"REPEATABLE-READ".equals(rows.getString(2)),"SOURCE_SNAPSHOT_NOT_READ_ONLY");
                }
            } else {
                connection.setTransactionIsolation(Connection.TRANSACTION_REPEATABLE_READ);connection.setReadOnly(true);connection.setAutoCommit(false);
                try(Statement statement=statement(connection,timeout);ResultSet rows=statement.executeQuery("SELECT current_database(), current_schema(), current_user")) {
                    check(rows.next()&&database.equals(rows.getString(1))&&"public".equals(rows.getString(2))&&database.equals(rows.getString(3)),"TARGET_NAMESPACE");
                }
            }
            return connection;
        } catch(Throwable error) { try {connection.close();}catch(SQLException ignored){} throw error; }
    }
    static Statement statement(Connection connection,int timeout) throws SQLException { Statement s=connection.createStatement(); s.setQueryTimeout(timeout); return s; }
    static PreparedStatement query(Connection connection,String sql,int timeout) throws SQLException { PreparedStatement s=connection.prepareStatement(sql,ResultSet.TYPE_FORWARD_ONLY,ResultSet.CONCUR_READ_ONLY); s.setQueryTimeout(timeout); s.setFetchSize(256); return s; }
    static List<Map<String,String>> rows(Connection connection,String sql,List<String> keys,List<String> params,int timeout) throws SQLException {
        List<Map<String,String>> result=new ArrayList<>();
        try(PreparedStatement statement=query(connection,sql,timeout)) {
            for(int i=0;i<params.size();i++) statement.setString(i+1,params.get(i));
            try(ResultSet rows=statement.executeQuery()) { while(rows.next()) { check(result.size()<20000,"SCHEMA_METADATA_LIMIT"); Map<String,String> row=new TreeMap<>(); for(String key:keys) row.put(key,rows.getString(key)); result.add(row); } }
        }
        result.sort(Comparator.comparing(MigrationCore.JSON::writeValueAsString)); return result;
    }
    static List<Map<String,String>> normalized(JsonNode list,List<String> keys) {
        check(list!=null&&list.isArray(),"METADATA_SHAPE"); List<Map<String,String>> result=new ArrayList<>();
        for(JsonNode item:list) { Map<String,String> row=new TreeMap<>(); for(String key:keys) { JsonNode value=item.get(key); check(value!=null,"METADATA_KEY"); row.put(key,value.isNull()?null:value.isString()?value.asString():value.toString()); } result.add(row); }
        result.sort(Comparator.comparing(MigrationCore.JSON::writeValueAsString)); return result;
    }
    static List<Table> sourceTables(Connection source,Config config,Map<String,Object> sourceShape) throws SQLException {
        byte[] bytes=MigrationCore.privateBytes(config.metadata,4*1024*1024); check(MigrationCore.hex(MigrationCore.sha(bytes)).equals(config.metadataSha),"METADATA_FILE_CHANGED");
        JsonNode manifest;
        try { manifest=MigrationCore.JSON.readTree(bytes); } catch(RuntimeException e) { throw new MigrationCore.Failure("METADATA_INVALID"); }
        List<Map<String,String>> expectedTables=normalized(manifest.get("tables"),TABLE_KEYS);
        check(expectedTables.stream().map(t->t.get("TABLE_SCHEMA")).collect(java.util.stream.Collectors.toSet()).equals(MigrationCore.DATABASES),"METADATA_DATABASE_SET");
        String where=" WHERE TABLE_SCHEMA IN ("+String.join(",",Collections.nCopies(7,"?"))+")";
        List<String> tableKeys=new ArrayList<>(TABLE_KEYS);tableKeys.add("AUTO_INCREMENT");
        List<Map<String,String>> liveTables=rows(source,"SELECT "+String.join(",",tableKeys)+" FROM information_schema.TABLES"+where,tableKeys,DATABASES,config.timeout);
        List<Map<String,String>> tables=new ArrayList<>();for(var live:liveTables){Map<String,String> structural=new TreeMap<>();for(String key:TABLE_KEYS)structural.put(key,live.get(key));tables.add(structural);}tables.sort(Comparator.comparing(MigrationCore.JSON::writeValueAsString));
        List<Map<String,String>> columns=rows(source,"SELECT "+String.join(",",COLUMN_KEYS)+" FROM information_schema.COLUMNS"+where,COLUMN_KEYS,DATABASES,config.timeout);
        List<Map<String,String>> indexes=rows(source,"SELECT "+String.join(",",INDEX_KEYS)+" FROM information_schema.STATISTICS"+where,INDEX_KEYS,DATABASES,config.timeout);
        check(tables.equals(expectedTables)&&columns.equals(normalized(manifest.get("columns"),COLUMN_KEYS))&&indexes.equals(normalized(manifest.get("indexes"),INDEX_KEYS)),"SOURCE_SCHEMA_CHANGED");
        for(Map<String,String> row:tables) check("BASE TABLE".equals(row.get("TABLE_TYPE"))&&"InnoDB".equals(row.get("ENGINE")),"SOURCE_TABLE_KIND");
        List<Map<String,String>> unsafe=rows(source,"SELECT TABLE_SCHEMA,TABLE_NAME,COLUMN_NAME FROM information_schema.KEY_COLUMN_USAGE"+where+" AND REFERENCED_TABLE_NAME IS NOT NULL",List.of("TABLE_SCHEMA","TABLE_NAME","COLUMN_NAME"),DATABASES,config.timeout);
        check(unsafe.isEmpty(),"SOURCE_FOREIGN_KEYS_UNSUPPORTED");
        var triggers=rows(source,"SELECT TRIGGER_SCHEMA,TRIGGER_NAME FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA IN ("+String.join(",",Collections.nCopies(7,"?"))+")",List.of("TRIGGER_SCHEMA","TRIGGER_NAME"),DATABASES,config.timeout);
        check(triggers.isEmpty(),"SOURCE_TRIGGERS_UNSUPPORTED");
        sourceShape.put("tables",tables); sourceShape.put("columns",columns); sourceShape.put("indexes",indexes);
        List<Table> result=new ArrayList<>();
        for(Map<String,String> table:tables) {
            String db=MigrationCore.database(table.get("TABLE_SCHEMA")),name=MigrationCore.identifier(table.get("TABLE_NAME"));
            List<Map<String,String>> selected=columns.stream().filter(c->db.equals(c.get("TABLE_SCHEMA"))&&name.equals(c.get("TABLE_NAME"))).sorted(Comparator.comparingInt(c->Integer.parseInt(c.get("ORDINAL_POSITION")))).toList();
            List<MigrationCore.Column> mapped=new ArrayList<>();
            for(Map<String,String> column:selected) {
                String extra=column.get("EXTRA");ordinaryColumn(column);
                mapped.add(new MigrationCore.Column(MigrationCore.identifier(column.get("COLUMN_NAME")),kind(column),"YES".equals(column.get("IS_NULLABLE")),extra!=null&&extra.contains("auto_increment")));
            }
            check(!mapped.isEmpty()&&mapped.size()<=512,"COLUMN_COUNT_LIMIT");
            String counter=liveTables.stream().filter(t->db.equals(t.get("TABLE_SCHEMA"))&&name.equals(t.get("TABLE_NAME"))).findFirst().orElseThrow().get("AUTO_INCREMENT");
            Long nextId=null;
            if(mapped.stream().anyMatch(MigrationCore.Column::identity)) {
                check(mapped.stream().filter(MigrationCore.Column::identity).count()==1&&counter!=null&&counter.matches("[0-9]{1,19}"),"SOURCE_IDENTITY_COUNTER");
                try{nextId=Long.parseLong(counter);}catch(NumberFormatException e){throw new MigrationCore.Failure("SOURCE_IDENTITY_RANGE");}check(nextId>=1,"SOURCE_IDENTITY_RANGE");
            } else check(counter==null,"SOURCE_IDENTITY_COUNTER");
            result.add(new Table(db,name,mapped,selected,nextId));
        }
        return result.stream().sorted(Comparator.comparing(Table::database).thenComparing(Table::name)).toList();
    }
    static void ordinaryColumn(Map<String,String> column) {
        String expression=column.get("GENERATION_EXPRESSION"),extra=column.get("EXTRA");
        check(expression==null||expression.isEmpty(),"GENERATED_COLUMN_UNSUPPORTED");
        check(extra==null||!extra.toUpperCase(Locale.ROOT).contains("VIRTUAL GENERATED")&&!extra.toUpperCase(Locale.ROOT).contains("STORED GENERATED"),"GENERATED_COLUMN_UNSUPPORTED");
    }
    static MigrationCore.Kind kind(Map<String,String> column) {
        String type=column.get("DATA_TYPE"),full=column.get("COLUMN_TYPE");
        return switch(type) {
            case "bit" -> { check("bit(1)".equals(full),"BIT_WIDTH_UNSUPPORTED");yield MigrationCore.Kind.BOOLEAN; }
            case "tinyint" -> {check(!full.contains("unsigned"),"UNSIGNED_INTEGER_UNSUPPORTED");yield "tinyint(1)".equals(full)?MigrationCore.Kind.BOOLEAN:MigrationCore.Kind.INTEGER;}
            case "smallint","mediumint","int","bigint" -> { check(!full.contains("unsigned"),"UNSIGNED_INTEGER_UNSUPPORTED");yield MigrationCore.Kind.INTEGER; }
            case "varchar","char","text","mediumtext","longtext" -> MigrationCore.Kind.STRING;
            case "datetime","timestamp" -> MigrationCore.Kind.TIMESTAMP;
            case "decimal","numeric" -> MigrationCore.Kind.DECIMAL;
            case "double","float" -> MigrationCore.Kind.FLOAT;
            case "json" -> MigrationCore.Kind.JSON;
            case "blob","mediumblob","longblob","binary","varbinary" -> MigrationCore.Kind.BINARY;
            default -> throw new MigrationCore.Failure("SOURCE_TYPE_UNSUPPORTED");
        };
    }
    static Shape targetShape(Connection target,String db,List<Table> tables,int timeout) throws SQLException {
        List<String> keys=List.of("table_name","column_name","ordinal_position","data_type","udt_name","is_nullable","character_maximum_length","numeric_precision","numeric_scale","datetime_precision","collation_schema","collation_name","is_identity","identity_generation","column_default","is_generated");
        var columns=rows(target,"SELECT "+String.join(",",keys)+" FROM information_schema.columns WHERE table_schema='public'",keys,List.of(),timeout);
        var targetTables=rows(target,"SELECT table_name,table_type FROM information_schema.tables WHERE table_schema='public'",List.of("table_name","table_type"),List.of(),timeout);
        Set<String> expected=tables.stream().filter(t->t.database.equals(db)).map(Table::name).collect(java.util.stream.Collectors.toSet());
        check(targetTables.stream().allMatch(t->"BASE TABLE".equals(t.get("table_type")))&&targetTables.stream().map(t->t.get("table_name")).collect(java.util.stream.Collectors.toSet()).equals(expected),"TARGET_TABLE_SET");
        var indexes=rows(target,"SELECT tablename,indexname,indexdef FROM pg_indexes WHERE schemaname='public'",List.of("tablename","indexname","indexdef"),List.of(),timeout);
        var sequences=rows(target,"SELECT sequencename,data_type,start_value,min_value,max_value,increment_by,cycle FROM pg_sequences WHERE schemaname='public'",List.of("sequencename","data_type","start_value","min_value","max_value","increment_by","cycle"),List.of(),timeout);
        var unsafe=rows(target,"SELECT c.relname AS table_name FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND NOT t.tgisinternal",List.of("table_name"),List.of(),timeout);
        check(unsafe.isEmpty(),"TARGET_TRIGGERS_UNSUPPORTED");
        Map<String,Map<String,String>> lookup=new TreeMap<>(); Set<String> identities=new TreeSet<>();
        for(var column:columns) { String table=MigrationCore.identifier(column.get("table_name")),name=MigrationCore.identifier(column.get("column_name")); lookup.put(table+"."+name,column); if("YES".equals(column.get("is_identity"))||Objects.toString(column.get("column_default"),"").startsWith("nextval(")) identities.add(table+"."+name); check("NEVER".equals(column.get("is_generated")),"TARGET_GENERATED_COLUMN"); }
        Set<String> ownedSequences=new TreeSet<>();
        for(String identity:identities) {
            String[] parts=identity.split("\\.");
            try(PreparedStatement statement=query(target,"SELECT pg_get_serial_sequence(?, ?)",timeout)) {
                statement.setString(1,"public."+MigrationCore.pg(parts[0]));statement.setString(2,parts[1]);
                try(ResultSet result=statement.executeQuery()) {
                    check(result.next(),"TARGET_SEQUENCE_OWNERSHIP");String sequence=result.getString(1);
                    check(sequence!=null&&sequence.matches("public\\.[a-z][a-z0-9_]{0,62}"),"TARGET_SEQUENCE_OWNERSHIP");check(ownedSequences.add(sequence.substring(7)),"TARGET_SHARED_SEQUENCE");
                }
            }
        }
        check(sequences.stream().map(s->s.get("sequencename")).collect(java.util.stream.Collectors.toSet()).equals(ownedSequences),"TARGET_SEQUENCE_SET");
        for(var sequence:sequences)check("1".equals(sequence.get("start_value"))&&"1".equals(sequence.get("min_value"))&&"1".equals(sequence.get("increment_by"))&&Set.of("f","false").contains(sequence.get("cycle")),"TARGET_SEQUENCE_POLICY");
        for(Table table:tables) if(table.database.equals(db)) {
            Set<String> actual=columns.stream().filter(c->table.name.equals(c.get("table_name"))).map(c->c.get("column_name")).collect(java.util.stream.Collectors.toSet());
            Set<String> names=table.columns.stream().map(MigrationCore.Column::name).collect(java.util.stream.Collectors.toSet());
            // 唯一特例：源 Seata 空 undo_log 可升级为官方 PG undo_log 的 generated id。
            Set<String> permitted=new HashSet<>(names); if(table.name.equals("undo_log")&&identities.contains("undo_log.id")) {
                Map<String,String> id=lookup.get("undo_log.id");check(Set.of("integer","bigint").contains(id.get("data_type"))&&"NO".equals(id.get("is_nullable")),"UNDO_LOG_GENERATED_ID");permitted.add("id");
            }
            check(actual.equals(permitted),"TARGET_COLUMN_SET");
            for(int i=0;i<table.columns.size();i++) compatible(table.columns.get(i),table.sourceColumns.get(i),lookup.get(table.name+"."+table.columns.get(i).name()));
            for(var column:table.columns) check(column.identity()==identities.contains(table.name+"."+column.name()),"TARGET_IDENTITY_MISMATCH");
        }
        Map<String,Object> shape=new TreeMap<>();shape.put("tables",targetTables);shape.put("columns",columns);shape.put("indexes",indexes);shape.put("sequences",sequences);
        return new Shape(MigrationCore.fingerprint(shape),identities,lookup);
    }
    static void compatible(MigrationCore.Column source,Map<String,String> original,Map<String,String> target) {
        check(target!=null&&source.nullable()=="YES".equals(target.get("is_nullable")),"TARGET_NULLABILITY"); String type=target.get("data_type");
        boolean allowed=switch(source.kind()) {
            case BOOLEAN -> type.equals("boolean"); case INTEGER -> Set.of("smallint","integer","bigint").contains(type);
            case STRING -> Set.of("character varying","text","character").contains(type);
            case TIMESTAMP -> type.equals("timestamp without time zone"); case DECIMAL -> type.equals("numeric");
            case FLOAT -> type.equals("double precision"); case JSON -> type.equals("jsonb"); case BINARY -> type.equals("bytea");
        };
        check(allowed,"TARGET_COLUMN_TYPE");
        if(source.kind()==MigrationCore.Kind.INTEGER) {
            int from=switch(original.get("DATA_TYPE")){case "tinyint"->8;case "smallint"->16;case "mediumint","int"->32;default->64;};
            int to=type.equals("smallint")?16:type.equals("integer")?32:64; check(to>=from,"TARGET_INTEGER_RANGE");
        }
        if(source.kind()==MigrationCore.Kind.STRING&&target.get("character_maximum_length")!=null) { String size=original.get("CHARACTER_MAXIMUM_LENGTH");check(size!=null&&Long.parseLong(target.get("character_maximum_length"))>=Long.parseLong(size),"TARGET_STRING_RANGE"); }
        if(source.kind()==MigrationCore.Kind.TIMESTAMP) check(Integer.parseInt(target.get("datetime_precision"))>=Integer.parseInt(Objects.toString(original.get("DATETIME_PRECISION"),"0")),"TARGET_TIMESTAMP_PRECISION");
        if(source.kind()==MigrationCore.Kind.DECIMAL && target.get("numeric_precision")!=null) check(Integer.parseInt(target.get("numeric_precision"))>=Integer.parseInt(original.get("NUMERIC_PRECISION"))&&Integer.parseInt(target.get("numeric_scale"))==Integer.parseInt(original.get("NUMERIC_SCALE")),"TARGET_DECIMAL_RANGE");
    }
    static Stat scan(Connection connection,Table table,boolean source,Config config,PreparedStatement insert) throws SQLException {
        String names=String.join(",",table.columns.stream().map(c->source?MigrationCore.mysql(c.name()):MigrationCore.pg(c.name())).toList());
        String namespace=source?MigrationCore.mysql(table.database):"public";
        String relation=namespace+"."+(source?MigrationCore.mysql(table.name):MigrationCore.pg(table.name));
        // 先在服务器上检查总行数与单行原始字节，避免驱动先缓存超大行才触发应用限制。
        String sizes=String.join("+",table.columns.stream().map(c->{String name=source?MigrationCore.mysql(c.name()):MigrationCore.pg(c.name());String value=source?"CAST("+name+" AS BINARY)":c.kind()==MigrationCore.Kind.BINARY?name:"CAST("+name+" AS text)";return "COALESCE(OCTET_LENGTH("+value+"),0)::bigint";}).map(s->source?s.replace("::bigint",""):s).toList());
        try(Statement statement=statement(connection,config.timeout);ResultSet rows=statement.executeQuery("SELECT count(*) AS n, COALESCE(MAX("+sizes+"),0) AS max_bytes FROM "+relation)) {
            check(rows.next(),"SIZE_CHECK_FAILED");long count=rows.getLong(1),maximum=rows.getLong(2);check(count>=0&&count<=config.maxRows,"ROW_COUNT_LIMIT");check(maximum>=0&&maximum<=config.maxRowBytes,"ROW_BYTES_LIMIT");
        }
        String sql="SELECT "+names+" FROM "+relation;
        MigrationCore.Rows fingerprint=new MigrationCore.Rows(config.maxRows,config.maxRowBytes);
        try(PreparedStatement statement=query(connection,sql,config.timeout);ResultSet rows=statement.executeQuery()) {
            while(rows.next()) {
                List<Object> values=new ArrayList<>(); int remaining=config.maxRowBytes;
                for(int i=0;i<table.columns.size();i++) { Object value=MigrationCore.read(rows,i+1,table.columns.get(i),remaining); remaining-=MigrationCore.bytes(table.columns.get(i).kind(),value).length;check(remaining>=0,"ROW_BYTES_LIMIT");values.add(value); }
                fingerprint.add(table.columns,values);
                if(insert!=null) { for(int i=0;i<values.size();i++) MigrationCore.bind(insert,i+1,table.columns.get(i),values.get(i));check(insert.executeUpdate()==1,"INSERT_ROW_COUNT"); }
            }
        }
        return new Stat(fingerprint.count(),fingerprint.finish());
    }
    static Map<String,Stat> stats(Connection connection,List<Table> tables,String db,boolean source,Config config) throws SQLException {
        Map<String,Stat> result=new TreeMap<>();for(Table table:tables)if(table.database.equals(db))result.put(table.name,scan(connection,table,source,config,null));return result;
    }
    static Map<String,Object> jsonStats(Map<String,Stat> stats) { Map<String,Object> result=new TreeMap<>(); stats.forEach((key,value)->result.put(key,value.json())); return result; }
    static Prepared prepare(Config config) throws SQLException {
        Connection source=null;Map<String,Connection> targets=new TreeMap<>();
        try {
            source=connect(config.source,true,DATABASES.get(0),config.timeout);
            Map<String,Object> shape=new TreeMap<>();List<Table> tables=sourceTables(source,config,shape);String sourceSha=MigrationCore.fingerprint(shape);
            String authority;
            try(Statement statement=statement(source,config.timeout);ResultSet rows=statement.executeQuery("SELECT @@server_uuid")){check(rows.next(),"SOURCE_IDENTITY");authority=MigrationCore.fingerprint(List.of(rows.getString(1),source.getMetaData().getURL()));}
            Map<String,Shape> shapes=new TreeMap<>();Map<String,Map<String,Stat>> sourceStats=new TreeMap<>();Map<String,Object> plan=new TreeMap<>();
            for(String db:DATABASES) {
                Connection target=connect(config.targets.get(db),false,db,config.timeout);targets.put(db,target);Shape targetShape=targetShape(target,db,tables,config.timeout);shapes.put(db,targetShape);
                Map<String,Stat> stat=stats(source,tables,db,true,config);check(stat.containsKey("undo_log")&&stat.get("undo_log").count==0,"SOURCE_UNDO_LOG_NOT_EMPTY");sourceStats.put(db,stat);
                plan.put(db,Map.of("source",jsonStats(stat),"target_schema_sha256",targetShape.sha256,"target_authority_sha256",MigrationCore.fingerprint(target.getMetaData().getURL())));
            }
            Map<String,Object> binding=new TreeMap<>();binding.put("databases",plan);binding.put("source_schema_sha256",sourceSha);binding.put("source_authority_sha256",authority);binding.put("source_identity_sha256",identitySha(tables));binding.put("metadata_sha256",config.metadataSha);binding.put("max_rows_per_table",config.maxRows);binding.put("max_row_bytes",config.maxRowBytes);binding.put("write_window_id",config.window);binding.put("migration_policy","v1-empty-undo-log-generated-id-only");
            return new Prepared(config,source,tables,sourceSha,authority,targets,shapes,sourceStats,MigrationCore.fingerprint(binding));
        } catch(Throwable error) { for(Connection c:targets.values())try{c.close();}catch(SQLException ignored){}if(source!=null)try{source.rollback();source.close();}catch(SQLException ignored){}throw error; }
    }
    static String identitySha(List<Table> tables) {
        Map<String,Long> counters=new TreeMap<>();for(Table table:tables)if(table.sourceNextId!=null)counters.put(table.database+"."+table.name,table.sourceNextId);return MigrationCore.fingerprint(counters);
    }
    static SequenceSetting sequenceSetting(Long sourceNextId,Long maximum) {
        if(maximum!=null&&maximum==Long.MAX_VALUE)return new SequenceSetting(Long.MAX_VALUE,true);
        long next=maximum==null?1:Math.max(1,maximum+1);if(sourceNextId!=null)next=Math.max(next,sourceNextId);return new SequenceSetting(next,false);
    }
    static Map<String,Object> report(Prepared prepared,String status,Map<String,String> receipts) {
        Map<String,Object> databases=new TreeMap<>();for(String db:DATABASES)databases.put(db,Map.of("tables",jsonStats(prepared.sourceStats.get(db)),"target_schema_sha256",prepared.shapes.get(db).sha256,"receipt",receipts.getOrDefault(db,"NONE")));
        return Map.of("status",status,"plan_sha256",prepared.planSha,"source_schema_sha256",prepared.sourceShape,"source_identity_sha256",identitySha(prepared.tables),"databases",databases,"migration_policy","v1-empty-undo-log-generated-id-only","write_window_required",true,"target_clients_must_remain_stopped",!status.equals("verified"));
    }
    static Map<String,Object> receipt(Prepared prepared,String db,String status) {
        return Map.of("schema_version",1,"database",db,"plan_sha256",prepared.planSha,"source_schema_sha256",prepared.sourceShape,"target_schema_sha256",prepared.shapes.get(db).sha256,"tables",jsonStats(prepared.sourceStats.get(db)),"status",status);
    }
    static Map<String,Object> readReceipt(Path path) {
        if(!Files.exists(path,LinkOption.NOFOLLOW_LINKS))return null;
        try { JsonNode node=MigrationCore.JSON.readTree(MigrationCore.privateBytes(path,262144));check(node.isObject(),"RECEIPT_INVALID");return MigrationCore.JSON.convertValue(node,Map.class); }
        catch(MigrationCore.Failure e){throw e;}catch(RuntimeException e){throw new MigrationCore.Failure("RECEIPT_INVALID");}
    }
    static void writeReceipt(Path path,Map<String,Object> next,Map<String,Object> expected) {
        Map<String,Object> current=readReceipt(path);check(MigrationCore.sameJson(current,expected),"RECEIPT_CHANGED");
        Path temporary=path.resolveSibling(".receipt-"+UUID.randomUUID()+".tmp");
        try {
            byte[] bytes=MigrationCore.JSON.writeValueAsBytes(next);
            try(FileChannel channel=FileChannel.open(temporary,Set.of(StandardOpenOption.CREATE_NEW,StandardOpenOption.WRITE),PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString("rw-------")))) {var buffer=java.nio.ByteBuffer.wrap(bytes);while(buffer.hasRemaining())channel.write(buffer);channel.force(true);}
            check(MigrationCore.sameJson(readReceipt(path),expected),"RECEIPT_CHANGED");Files.move(temporary,path,StandardCopyOption.ATOMIC_MOVE,StandardCopyOption.REPLACE_EXISTING);
            try(FileChannel directory=FileChannel.open(path.getParent(),StandardOpenOption.READ)){directory.force(true);}
        } catch(MigrationCore.Failure e){throw e;}catch(Exception e){throw new MigrationCore.Failure("RECEIPT_WRITE_FAILED");}finally{try{Files.deleteIfExists(temporary);}catch(IOException ignored){}}
    }
    static String guardReceipt(Prepared prepared,String db,Map<String,Object> existing,Map<String,Stat> target,boolean resume) {
        if(existing==null){check(target.values().stream().allMatch(s->s.count==0),"TARGET_NOT_EMPTY");return "NEW";}
        check(resume,"EXPLICIT_RESUME_REQUIRED");
        Object status=existing.get("status");check(status instanceof String&&Set.of("PREPARED","COMMIT_INTENT","COMMITTED","VERIFIED").contains(status),"RECEIPT_STATUS");
        Map<String,Object> expected=receipt(prepared,db,(String)status);check(MigrationCore.sameJson(existing,expected),"RECEIPT_BINDING_MISMATCH");
        if(target.equals(prepared.sourceStats.get(db)))return "RECOVER_VERIFIED";
        check(Set.of("PREPARED","COMMIT_INTENT").contains(status)&&target.values().stream().allMatch(s->s.count==0),"RESUME_CONTENT_MISMATCH");return "RETRY_EMPTY";
    }
    static void calibrate(Prepared prepared,String db) throws SQLException {
        Connection target=prepared.targets.get(db);target.rollback();target.setReadOnly(false);target.setAutoCommit(true);
        for(String identity:prepared.shapes.get(db).identityColumns) {
            String[] parts=identity.split("\\.");String table=parts[0],column=parts[1];
            // 只接受驱动元数据指认的序列；表和列均已通过固定标识符校验。
            Long maximum=null;
            try(Statement statement=statement(target,prepared.config.timeout);ResultSet rows=statement.executeQuery("SELECT max("+MigrationCore.pg(column)+") FROM public."+MigrationCore.pg(table))) {check(rows.next(),"IDENTITY_MAXIMUM");long value=rows.getLong(1);if(!rows.wasNull())maximum=value;}
            Table original=prepared.tables.stream().filter(t->t.database.equals(db)&&t.name.equals(table)).findFirst().orElseThrow();SequenceSetting setting=sequenceSetting(original.sourceNextId,maximum);
            try(PreparedStatement statement=query(target,"SELECT setval(pg_get_serial_sequence(?, ?), ?, ?)",prepared.config.timeout)) {
                statement.setString(1,"public."+MigrationCore.pg(table));statement.setString(2,column);statement.setLong(3,setting.value);statement.setBoolean(4,setting.called);
                try(ResultSet rows=statement.executeQuery()){check(rows.next(),"IDENTITY_CALIBRATION");long assigned=rows.getLong(1);check(!rows.wasNull()&&assigned==setting.value,"IDENTITY_CALIBRATION");}
            }
        }
    }
    static Map<String,Object> run(Prepared prepared,boolean execute,boolean resume,String expectedPlan) throws SQLException {
        if(execute)check(prepared.planSha.equals(expectedPlan),"PLAN_CHANGED");
        Map<String,String> states=new TreeMap<>();Map<String,Map<String,Object>> old=new TreeMap<>();Map<String,String> actions=new TreeMap<>();
        // 所有目标先通过空表/受管恢复守卫，避免第七库才发现未知现有数据。
        for(String db:DATABASES) {
            Map<String,Stat> target=stats(prepared.targets.get(db),prepared.tables,db,false,prepared.config);Map<String,Object> prior=readReceipt(prepared.config.receipts.resolve(db+".receipt.json"));
            old.put(db,prior);actions.put(db,guardReceipt(prepared,db,prior,target,resume));states.put(db,prior==null?"NONE":(String)prior.get("status"));
        }
        if(!execute)return report(prepared,"planned",states);
        for(String db:DATABASES) {
            Path path=prepared.config.receipts.resolve(db+".receipt.json");Map<String,Object> prior=old.get(db);
            if(actions.get(db).equals("RECOVER_VERIFIED")) {
                calibrate(prepared,db);Map<String,Object> done=receipt(prepared,db,"VERIFIED");writeReceipt(path,done,prior);states.put(db,"VERIFIED");continue;
            }
            Map<String,Object> started=receipt(prepared,db,"PREPARED");writeReceipt(path,started,prior);
            Connection target=prepared.targets.get(db);target.rollback();
            MigrationCore.transaction(target,()->{
                // 重查全库并锁住每张目标表，阻止迁移期间意外业务写入。
                for(Table table:prepared.tables)if(table.database.equals(db))try(Statement statement=statement(target,prepared.config.timeout)){statement.execute("LOCK TABLE public."+MigrationCore.pg(table.name)+" IN ACCESS EXCLUSIVE MODE");}
                check(stats(target,prepared.tables,db,false,prepared.config).values().stream().allMatch(s->s.count==0),"TARGET_NOT_EMPTY");
                check(targetShape(target,db,prepared.tables,prepared.config.timeout).sha256.equals(prepared.shapes.get(db).sha256),"TARGET_SCHEMA_CHANGED");
                for(Table table:prepared.tables)if(table.database.equals(db)) {
                    String names=String.join(",",table.columns.stream().map(c->MigrationCore.pg(c.name())).toList());String placeholders=String.join(",",Collections.nCopies(table.columns.size(),"?"));
                    String overriding=table.columns.stream().anyMatch(MigrationCore.Column::identity)?" OVERRIDING SYSTEM VALUE":"";
                    String sql="INSERT INTO public."+MigrationCore.pg(table.name)+" ("+names+")"+overriding+" VALUES ("+placeholders+")";
                    try(PreparedStatement insert=target.prepareStatement(sql)){insert.setQueryTimeout(prepared.config.timeout);Stat copied=scan(prepared.source,table,true,prepared.config,insert);check(copied.equals(prepared.sourceStats.get(db).get(table.name)),"SOURCE_CONTENT_CHANGED");}
                }
                check(stats(target,prepared.tables,db,false,prepared.config).equals(prepared.sourceStats.get(db)),"TARGET_CONTENT_MISMATCH");
                writeReceipt(path,receipt(prepared,db,"COMMIT_INTENT"),started);return null;
            });
            Map<String,Object> intent=receipt(prepared,db,"COMMIT_INTENT"),committed=receipt(prepared,db,"COMMITTED");writeReceipt(path,committed,intent);
            calibrate(prepared,db);writeReceipt(path,receipt(prepared,db,"VERIFIED"),committed);states.put(db,"VERIFIED");
        }
        return report(prepared,"verified",states);
    }
    static FileChannel lock(Config config) throws IOException {
        Path path=config.receipts.resolve("migration.lock");
        if(Files.exists(path,LinkOption.NOFOLLOW_LINKS))MigrationCore.privateBytes(path,128);
        return FileChannel.open(path,Set.of(StandardOpenOption.CREATE,StandardOpenOption.WRITE,LinkOption.NOFOLLOW_LINKS),PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString("rw-------")));
    }
    public static void main(String[] args) {
        Map<String,Object> result;int code=0;
        try {
            java.util.logging.Logger.getLogger("org.postgresql").setLevel(java.util.logging.Level.OFF);
            Path file=null;String expected=null;boolean execute=false,resume=false;
            for(int i=0;i<args.length;i++)switch(args[i]){case "--config"->file=Path.of(args[++i]);case "--execute"->execute=true;case "--resume"->resume=true;case "--expected-plan-sha256"->expected=args[++i];default->throw new MigrationCore.Failure("ARGUMENT_REJECTED");}
            check(file!=null,"CONFIG_REQUIRED");check(!execute||expected!=null&&expected.matches("[0-9a-f]{64}"),"EXPECTED_PLAN_REQUIRED");check(!resume||execute,"RESUME_REQUIRES_EXECUTE");
            Config config=config(file);
            if(execute) {
                try(FileChannel channel=lock(config);FileLock held=channel.tryLock()) {check(held!=null,"MIGRATION_ALREADY_RUNNING");try(Prepared prepared=prepare(config)){result=run(prepared,true,resume,expected);}}
            } else try(Prepared prepared=prepare(config)){result=run(prepared,false,false,null);}
        } catch(MigrationCore.Failure e){result=e.report();code=1;}
        catch(SQLException e){result=new MigrationCore.Failure("JDBC_OPERATION_FAILED",e).report();code=1;}
        catch(Throwable e){result=Map.of("status","rejected","reason","MIGRATION_INTERNAL_FAILURE");code=1;}
        System.out.println(MigrationCore.JSON.writeValueAsString(result));if(code!=0)System.exit(code);
    }
}
