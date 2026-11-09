import java.lang.reflect.*;
import java.math.BigDecimal;
import java.nio.file.*;
import java.nio.file.attribute.PosixFilePermissions;
import java.sql.*;
import java.time.LocalDateTime;
import java.util.*;

/** 全部数据与 JDBC 行为均为合成 fixture；不加载或连接真实数据库。 */
public class MigrationContractTest {
    static int tests;
    static final String SECRET="SYNTHETIC_PASSWORD_URL_SQL_PARAMETER_MUST_NOT_ESCAPE";
    static void check(boolean condition) { if(!condition)throw new AssertionError("SYNTHETIC_ASSERTION_FAILED"); }
    static void test(Runnable run) { run.run();tests++; }
    static void rejected(String code,Runnable run) { try {run.run();throw new AssertionError("EXPECTED_REJECTION");}catch(MigrationCore.Failure e){check(e.reason.equals(code));check(!MigrationCore.JSON.writeValueAsString(e.report()).contains(SECRET));} }
    static MigrationCore.Column column(String name,MigrationCore.Kind kind) {return new MigrationCore.Column(name,kind,true,false);}
    static String rowHash(List<MigrationCore.Column> columns,List<Object> values) {MigrationCore.Rows rows=new MigrationCore.Rows(10,1024*1024);rows.add(columns,values);return rows.finish();}
    static void codecs() {
        test(()->check(MigrationCore.normalize(MigrationCore.Kind.BOOLEAN,new byte[]{1},1024).equals(true)));
        test(()->{rejected("BOOLEAN_VALUE",()->MigrationCore.normalize(MigrationCore.Kind.BOOLEAN,2,1024));rejected("BOOLEAN_TYPE",()->MigrationCore.normalize(MigrationCore.Kind.BOOLEAN,"false",1024));});
        test(()->{check(MigrationCore.normalize(MigrationCore.Kind.INTEGER,new BigDecimal("9223372036854775807"),1024).equals(Long.MAX_VALUE));rejected("VALUE_CONVERSION_FAILED",()->MigrationCore.normalize(MigrationCore.Kind.INTEGER,new BigDecimal("9223372036854775808"),1024));});
        test(()->{Object opaque=new Object(){public String toString(){throw new AssertionError("MUST_NOT_STRINGIFY");}};rejected("INTEGER_TYPE",()->MigrationCore.normalize(MigrationCore.Kind.INTEGER,opaque,1024));});
        test(()->{check(MigrationCore.normalize(MigrationCore.Kind.DECIMAL,new BigDecimal("1000.12000"),1024).equals(new BigDecimal("1000.12")));check(MigrationCore.normalize(MigrationCore.Kind.DECIMAL,new BigDecimal("-0.00"),1024).equals(BigDecimal.ZERO));});
        test(()->{LocalDateTime time=LocalDateTime.of(2026,11,9,13,2,1,123456000);check(MigrationCore.normalize(MigrationCore.Kind.TIMESTAMP,time,1024).equals(time));rejected("TIMESTAMP_PRECISION",()->MigrationCore.normalize(MigrationCore.Kind.TIMESTAMP,time.plusNanos(1),1024));});
        test(()->{String json=MigrationCore.canonicalJson("{\"z\":[null,\"NULL\",true,1.00],\"a\":9007199254740993.123456789}",4096);check(json.equals("{\"a\":9007199254740993.123456789,\"z\":[null,\"NULL\",true,1]}"));check(MigrationCore.canonicalJson("[1,2]",1024).equals("[1,2]"));check(!MigrationCore.canonicalJson("[1,2]",1024).equals(MigrationCore.canonicalJson("[2,1]",1024)));});
        test(()->{rejected("JSON_INVALID",()->MigrationCore.canonicalJson("{\"a\":1,\"a\":2}",1024));rejected("JSON_NUMBER_RANGE",()->MigrationCore.canonicalJson("1e1000000",1024));rejected("JSON_NUL_UNSUPPORTED",()->MigrationCore.canonicalJson("\"\\u0000\"",1024));});
        test(()->{rejected("TEXT_ENCODING_INVALID",()->MigrationCore.normalize(MigrationCore.Kind.STRING,"\uD800",1024));rejected("TEXT_ENCODING_INVALID",()->MigrationCore.canonicalJson("\"\\uD800\"",1024));});
        test(()->{byte[] bytes={0,1,-1};byte[] copy=(byte[])MigrationCore.normalize(MigrationCore.Kind.BINARY,bytes,1024);bytes[0]=9;check(copy[0]==0);rejected("ROW_BYTES_LIMIT",()->MigrationCore.normalize(MigrationCore.Kind.STRING,"中文",4));});
        test(()->{rejected("FLOAT_NONFINITE",()->MigrationCore.normalize(MigrationCore.Kind.FLOAT,Double.NaN,1024));rejected("FLOAT_NONFINITE",()->MigrationCore.normalize(MigrationCore.Kind.FLOAT,Double.POSITIVE_INFINITY,1024));});
        test(()->{var columns=List.of(column("first",MigrationCore.Kind.STRING),column("second",MigrationCore.Kind.STRING));check(!rowHash(columns,Arrays.asList(null,"NULL")).equals(rowHash(columns,Arrays.asList("NULL",null))));check(!rowHash(columns,List.of("ab","c")).equals(rowHash(columns,List.of("a","bc"))));});
        test(()->{var columns=List.of(column("id",MigrationCore.Kind.INTEGER));MigrationCore.Rows a=new MigrationCore.Rows(3,1024),b=new MigrationCore.Rows(3,1024);a.add(columns,List.of(1L));a.add(columns,List.of(2L));b.add(columns,List.of(2L));b.add(columns,List.of(1L));check(a.finish().equals(b.finish()));a.add(columns,List.of(1L));check(!a.finish().equals(b.finish()));rejected("ROW_COUNT_LIMIT",()->a.add(columns,List.of(3L)));});
        test(()->{check(MigrationCore.fingerprint(Map.of("b",Map.of("x",1L),"a",2)).equals(MigrationCore.fingerprint(Map.of("a",2L,"b",Map.of("x",1)))));rejected("IDENTIFIER_REJECTED",()->MigrationCore.pg("x\";DROP TABLE data;--"));rejected("DATABASE_NOT_ALLOWED",()->MigrationCore.database("company_dev_ai"));});
        test(()->{SQLException failure=new SQLException(SECRET,"28000",1045,new RuntimeException(SECRET));String output=MigrationCore.JSON.writeValueAsString(new MigrationCore.Failure("JDBC_OPERATION_FAILED",failure).report());check(!output.contains(SECRET)&&output.contains("28000")&&output.contains("1045"));});
        test(()->{Properties p=new Properties();p.setProperty("url","jdbc:postgresql://synthetic.invalid/github_dev_ai?sslmode=verify-full");check(FleetMigration.url(p,false,"github_dev_ai").contains("verify-full"));p.setProperty("url","jdbc:postgresql://user:pass@synthetic.invalid/github_dev_ai");rejected("JDBC_URL_AUTHORITY",()->FleetMigration.url(p,false,"github_dev_ai"));p.setProperty("url","jdbc:postgresql://synthetic.invalid/company");rejected("JDBC_DATABASE_MISMATCH",()->FleetMigration.url(p,false,"github_dev_ai"));p.setProperty("url","jdbc:postgresql://synthetic.invalid/github_dev_ai?password="+SECRET);rejected("JDBC_URL_OPTION",()->FleetMigration.url(p,false,"github_dev_ai"));});
        test(()->{Properties p=new Properties();p.setProperty("url","jdbc:postgresql://synthetic.invalid/github_dev_ai?socketTimeout=0");rejected("JDBC_TIMEOUT_RANGE",()->FleetMigration.url(p,false,"github_dev_ai"));p.setProperty("url","jdbc:mysql://synthetic.invalid/github_dev_ai?useSSL="+SECRET);rejected("JDBC_BOOLEAN_OPTION",()->FleetMigration.url(p,true,"github_dev_ai"));});
        test(()->{FleetMigration.ordinaryColumn(Map.of("EXTRA","DEFAULT_GENERATED on update CURRENT_TIMESTAMP","GENERATION_EXPRESSION",""));rejected("GENERATED_COLUMN_UNSUPPORTED",()->FleetMigration.ordinaryColumn(Map.of("EXTRA","STORED GENERATED","GENERATION_EXPRESSION","")));check(FleetMigration.kind(Map.of("DATA_TYPE","bit","COLUMN_TYPE","bit(1)"))==MigrationCore.Kind.BOOLEAN);rejected("UNSIGNED_INTEGER_UNSUPPORTED",()->FleetMigration.kind(Map.of("DATA_TYPE","tinyint","COLUMN_TYPE","tinyint unsigned")));});
        test(()->{check(FleetMigration.sequenceSetting(100L,7L).equals(new FleetMigration.SequenceSetting(100,false)));check(FleetMigration.sequenceSetting(1L,7L).equals(new FleetMigration.SequenceSetting(8,false)));check(FleetMigration.sequenceSetting(100L,null).equals(new FleetMigration.SequenceSetting(100,false)));check(FleetMigration.sequenceSetting(null,null).equals(new FleetMigration.SequenceSetting(1,false)));check(FleetMigration.sequenceSetting(Long.MAX_VALUE,Long.MAX_VALUE).equals(new FleetMigration.SequenceSetting(Long.MAX_VALUE,true)));});
        test(()->{
            Map<Integer,Object> bound=new TreeMap<>();List<String> methods=new ArrayList<>();
            PreparedStatement statement=(PreparedStatement)Proxy.newProxyInstance(MigrationContractTest.class.getClassLoader(),new Class[]{PreparedStatement.class},(p,m,a)->{methods.add(m.getName());if(a!=null&&a.length>=2)bound.put((Integer)a[0],a[1]);return fallback(m);});
            try {
                String quoted="literal 'NULL' \\\" quote; -- still data";MigrationCore.bind(statement,1,column("value",MigrationCore.Kind.STRING),quoted);
                MigrationCore.bind(statement,2,column("value",MigrationCore.Kind.STRING),null);
                String json=MigrationCore.canonicalJson("[null,\"NULL\",1.000,\"a'b\"]",1024);MigrationCore.bind(statement,3,column("value",MigrationCore.Kind.JSON),json);
                check(bound.get(1).equals(quoted)&&methods.get(0).equals("setObject")&&methods.get(1).equals("setNull"));
                check(bound.get(3)instanceof org.postgresql.util.PGobject object&&object.getType().equals("jsonb")&&object.getValue().equals("[null,\"NULL\",1,\"a'b\"]"));
            }catch(SQLException e){throw new AssertionError("BIND_FAILED");}
        });
        test(()->{
            Map<String,Object> data=new HashMap<>();data.put("text","NULL 'quote' 中文");data.put("binary",new byte[]{0,-1});data.put("time",LocalDateTime.of(2026,1,1,0,0,0,999999000));data.put("nullable",null);
            ResultSet rows=result(List.of(data),List.of("text","binary","time","nullable"));
            try {check(rows.next());check(MigrationCore.read(rows,1,column("text",MigrationCore.Kind.STRING),1024).equals(data.get("text")));check(Arrays.equals((byte[])MigrationCore.read(rows,2,column("binary",MigrationCore.Kind.BINARY),1024),(byte[])data.get("binary")));check(MigrationCore.read(rows,3,column("time",MigrationCore.Kind.TIMESTAMP),1024).equals(data.get("time")));check(MigrationCore.read(rows,4,column("nullable",MigrationCore.Kind.STRING),1024)==null);}catch(SQLException e){throw new AssertionError("READ_FAILED");}
        });
    }
    static void privateFiles() throws Exception {
        Path dir=Files.createTempDirectory("migration-contract-").toRealPath();dir.toFile().deleteOnExit();Files.setPosixFilePermissions(dir,PosixFilePermissions.fromString("rwx------"));
        Path file=dir.resolve("credentials.properties");Files.writeString(file,"url=jdbc:postgresql://synthetic.invalid/github_dev_ai\nuser=github_dev_ai\npassword=quote'\\=NULL中文\n");Files.setPosixFilePermissions(file,PosixFilePermissions.fromString("rw-------"));
        test(()->check(MigrationCore.properties(file).getProperty("password").equals("quote'=NULL中文")));
        Path link=dir.resolve("link.properties");Files.createSymbolicLink(link,file);test(()->rejected("PRIVATE_FILE_TYPE",()->MigrationCore.properties(link)));
        Files.setPosixFilePermissions(file,PosixFilePermissions.fromString("rw-r--r--"));test(()->rejected("PRIVATE_FILE_PERMISSION",()->MigrationCore.properties(file)));
        Files.setPosixFilePermissions(file,PosixFilePermissions.fromString("rw-------"));Files.writeString(file,"url=x\nurl=y\nuser=x\npassword="+SECRET);test(()->rejected("DUPLICATE_PROPERTY",()->MigrationCore.properties(file)));
        Path receipt=dir.resolve("state.json");Map<String,Object> first=Map.of("count",1L,"status","PREPARED");FleetMigration.writeReceipt(receipt,first,null);
        test(()->{check(MigrationCore.sameJson(FleetMigration.readReceipt(receipt),first));FleetMigration.writeReceipt(receipt,Map.of("status","COMMITTED","count",1),first);rejected("RECEIPT_CHANGED",()->FleetMigration.writeReceipt(receipt,first,first));});
    }
    static Object fallback(Method method) {Class<?> type=method.getReturnType();if(type==boolean.class)return false;if(type==int.class)return 0;if(type==long.class)return 0L;return null;}
    static ResultSet result(List<Map<String,Object>> data,List<String> names) {
        int[] cursor={-1};boolean[] wasNull={false};
        return (ResultSet)Proxy.newProxyInstance(MigrationContractTest.class.getClassLoader(),new Class[]{ResultSet.class},(proxy,method,args)->{
            if(method.getName().equals("next"))return ++cursor[0]<data.size();if(method.getName().equals("wasNull"))return wasNull[0];
            if(method.getName().startsWith("get")&&args!=null&&args.length>0&&(args[0]instanceof String||args[0]instanceof Integer)) {
                String name=args[0]instanceof String?(String)args[0]:names.get((Integer)args[0]-1);Object value=data.get(cursor[0]).get(name);wasNull[0]=value==null;
                if(method.getName().equals("getString"))return value==null?null:value.toString();
                if(method.getName().equals("getCharacterStream"))return value==null?null:new java.io.StringReader((String)value);
                if(method.getName().equals("getBinaryStream"))return value==null?null:new java.io.ByteArrayInputStream((byte[])value);
                if(method.getName().equals("getBigDecimal"))return value==null?null:new BigDecimal(value.toString());
                if(method.getName().equals("getLong"))return value==null?0L:((Number)value).longValue();return value;
            }return fallback(method);
        });
    }
    static final class Jdbc implements InvocationHandler {
        final List<Map<String,Object>> committed=new ArrayList<>(),staged=new ArrayList<>();boolean failInsert;int insertCalls,commits,rollbacks,locks,dataReads;Long maximumBytes;final boolean source;
        final Connection connection;
        Jdbc(boolean source){this.source=source;if(source)committed.add(Map.of("id",7L));connection=(Connection)Proxy.newProxyInstance(getClass().getClassLoader(),new Class[]{Connection.class},this);}
        public Object invoke(Object proxy,Method method,Object[] args)throws Throwable {
            switch(method.getName()) {
                case "prepareStatement":return statement((String)args[0],true);
                case "createStatement":return statement(null,false);
                case "commit":committed.addAll(staged);staged.clear();commits++;return null;
                case "rollback":staged.clear();rollbacks++;return null;
                default:return fallback(method);
            }
        }
        Object statement(String sql,boolean prepared) {
            Map<Integer,Object> parameters=new TreeMap<>();
            return Proxy.newProxyInstance(getClass().getClassLoader(),new Class[]{prepared?PreparedStatement.class:Statement.class},(proxy,method,args)->{
                String query=sql==null&&args!=null&&args.length>0&&args[0]instanceof String?(String)args[0]:sql;
                if(method.getName().equals("execute")){if(query.startsWith("LOCK TABLE"))locks++;return false;}
                if(method.getName().equals("executeUpdate")){insertCalls++;if(failInsert)throw new SQLException(SECRET,"22001",99);staged.add(Map.of("id",parameters.get(1)));return 1;}
                if(method.getName().equals("executeQuery"))return select(query);
                if(method.getName().startsWith("set")&&args!=null&&args.length==2&&args[0]instanceof Integer)parameters.put((Integer)args[0],args[1]);
                return fallback(method);
            });
        }
        ResultSet select(String sql) {
            if(sql.startsWith("SELECT count(*)"))return result(List.of(Map.of("n",(long)(committed.size()+staged.size()),"max_bytes",maximumBytes==null?1L:maximumBytes)),List.of("n","max_bytes"));
            if(sql.contains("information_schema.tables"))return result(List.of(Map.of("table_name","fixture","table_type","BASE TABLE")),List.of());
            if(sql.contains("information_schema.columns")) {
                Map<String,Object> c=new HashMap<>();c.put("table_name","fixture");c.put("column_name","id");c.put("ordinal_position",1);c.put("data_type","bigint");c.put("udt_name","int8");c.put("is_nullable","NO");c.put("numeric_precision",64);c.put("numeric_scale",0);c.put("is_identity","NO");c.put("is_generated","NEVER");return result(List.of(c),List.of());
            }
            if(sql.contains("pg_indexes")||sql.contains("pg_trigger")||sql.contains("pg_sequences"))return result(List.of(),List.of());
            if(sql.startsWith("SELECT ")){dataReads++;List<Map<String,Object>> rows=new ArrayList<>(committed);rows.addAll(staged);return result(rows,List.of("id"));}
            throw new AssertionError("UNEXPECTED_SYNTHETIC_SQL");
        }
    }
    static void migration() throws Exception {
        Path dir=Files.createTempDirectory("migration-transaction-").toRealPath();Files.setPosixFilePermissions(dir,PosixFilePermissions.fromString("rwx------"));
        var config=new FleetMigration.Config(null,Map.of(),null,"0".repeat(64),dir,100,4096,10,"synthetic-window");
        var column=new MigrationCore.Column("id",MigrationCore.Kind.INTEGER,false,false);List<FleetMigration.Table> tables=new ArrayList<>();
        Map<String,Jdbc> jdbc=new TreeMap<>();Map<String,Connection> targets=new TreeMap<>();Map<String,FleetMigration.Shape> shapes=new TreeMap<>();Map<String,Map<String,FleetMigration.Stat>> sourceStats=new TreeMap<>();
        Jdbc source=new Jdbc(true);
        for(String db:FleetMigration.DATABASES){Jdbc target=new Jdbc(false);jdbc.put(db,target);targets.put(db,target.connection);tables.add(new FleetMigration.Table(db,"fixture",List.of(column),List.of(Map.of("DATA_TYPE","bigint"))));}
        for(String db:FleetMigration.DATABASES){shapes.put(db,FleetMigration.targetShape(targets.get(db),db,tables,10));sourceStats.put(db,FleetMigration.stats(source.connection,tables,db,true,config));}
        var prepared=new FleetMigration.Prepared(config,source.connection,tables,"1".repeat(64),"2".repeat(64),targets,shapes,sourceStats,"3".repeat(64));
        test(()->{int before=source.dataReads;source.maximumBytes=4097L;rejected("ROW_BYTES_LIMIT",()->{try{FleetMigration.scan(source.connection,tables.get(0),true,config,null);}catch(SQLException e){throw new AssertionError("UNEXPECTED_SIZE_SQL");}});check(source.dataReads==before);source.maximumBytes=null;});
        test(()->{try {check(FleetMigration.run(prepared,false,false,null).get("status").equals("planned"));check(jdbc.values().stream().allMatch(j->j.insertCalls==0&&j.commits==0&&j.locks==0));check(Files.list(dir).findAny().isEmpty());}catch(Exception e){throw new AssertionError("PLAN_TEST_FAILED");}});
        String first=FleetMigration.DATABASES.get(0),second=FleetMigration.DATABASES.get(1);jdbc.get(second).failInsert=true;
        test(()->{try{FleetMigration.run(prepared,true,false,prepared.planSha());throw new AssertionError("EXPECTED_SQL_FAILURE");}catch(SQLException error){check(error.getSQLState().equals("22001"));check(jdbc.get(first).committed.size()==1&&jdbc.get(second).committed.isEmpty()&&jdbc.get(second).staged.isEmpty()&&jdbc.get(second).rollbacks>0);check(FleetMigration.readReceipt(dir.resolve(first+".receipt.json")).get("status").equals("VERIFIED"));}});
        test(()->rejected("EXPLICIT_RESUME_REQUIRED",()->{try{FleetMigration.run(prepared,true,false,prepared.planSha());}catch(SQLException e){throw new AssertionError("WRONG_SQL_FAILURE");}}));
        jdbc.get(second).failInsert=false;
        test(()->{try{check(FleetMigration.run(prepared,true,true,prepared.planSha()).get("status").equals("verified"));check(jdbc.get(first).insertCalls==1);check(jdbc.values().stream().allMatch(j->j.committed.size()==1&&j.staged.isEmpty()));}catch(SQLException e){throw new AssertionError("RESUME_FAILED");}});
        test(()->{try{Map<String,Object> valid=FleetMigration.readReceipt(dir.resolve(first+".receipt.json"));Map<String,Object> wrong=new HashMap<>(valid);wrong.put("plan_sha256","9".repeat(64));rejected("RECEIPT_BINDING_MISMATCH",()->FleetMigration.guardReceipt(prepared,first,wrong,sourceStats.get(first),true));Map<String,FleetMigration.Stat> foreign=Map.of("fixture",new FleetMigration.Stat(2,"0".repeat(64)));rejected("TARGET_NOT_EMPTY",()->FleetMigration.guardReceipt(prepared,first,null,foreign,false));}catch(Exception e){if(e instanceof RuntimeException r)throw r;throw new AssertionError("GUARD_FAILED");}});
        test(()->{
            Map<String,Object> intent=FleetMigration.receipt(prepared,first,"COMMIT_INTENT");
            check(FleetMigration.guardReceipt(prepared,first,intent,sourceStats.get(first),true).equals("RECOVER_VERIFIED"));
            Map<String,FleetMigration.Stat> empty=Map.of("fixture",new FleetMigration.Stat(0,"0".repeat(64)));
            check(FleetMigration.guardReceipt(prepared,first,intent,empty,true).equals("RETRY_EMPTY"));
            rejected("RESUME_CONTENT_MISMATCH",()->FleetMigration.guardReceipt(prepared,first,FleetMigration.receipt(prepared,first,"COMMITTED"),empty,true));
        });
    }
    public static void main(String[] args) throws Exception {codecs();privateFiles();migration();System.out.println(MigrationCore.JSON.writeValueAsString(Map.of("tests",tests,"failures",0,"canonical_probe",MigrationCore.fingerprint(Map.of("z",List.of(Map.of("count",1L),Map.of("x","NULL")),"a",7)))));}
}
