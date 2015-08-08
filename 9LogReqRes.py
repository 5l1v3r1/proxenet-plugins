"""
Stores all traffic in a SQLite db
Note: requests & responses are stored as is (i.e. might be binary, compressed, etc)

For merging multiple SQLite databases generated by this plugin, use
proxenet-logreqres-merge.py (https://gist.github.com/hugsy/1cad97ed7cd68cc87c8a) to merge
them.

"""

import os, sqlite3, time, ConfigParser

PLUGIN_NAME = "LogReqRes"
AUTHOR = "hugsy"

HOME = os.getenv( "HOME" )
CONFIG_FILE = os.getenv("HOME") + "/.proxenet.ini"

try:
    option_name = "path_to_logdb"
    config = ConfigParser.ConfigParser()
    config.read(CONFIG_FILE)
    dbpath = os.path.realpath( config.get(PLUGIN_NAME, option_name, 0, {"home": os.getenv("HOME")}) )
    if not os.path.exists(dbpath):
        raise Exception("falling back to autogen db")
    dbname = dbpath + "/proxenet-"+str( int(time.time()) )+".db"
except Exception as e:
    dbname = "/tmp/proxenet-"+str( int(time.time()) )+".db"
    print("[-] Could not find '%s/%s' option in '%s', using default '%s'" % (PLUGIN_NAME, option_name, CONFIG_FILE, dbname))


class SqliteDb:
    def __init__(self, dbname):
        print("[%s] HTTP traffic will be stored in '%s'" % (PLUGIN_NAME, dbname))
        self.data_file = dbname
        self.execute("CREATE TABLE requests  (id INTEGER, request BLOB, uri TEXT, timestamp INTEGER, comment TEXT DEFAULT NULL)")
        self.execute("CREATE TABLE responses (id INTEGER, response BLOB,  uri TEXT, timestamp INTEGER, comment TEXT DEFAULT NULL)")
        return

    def connect(self):
        self.conn = sqlite3.connect(self.data_file)
        self.conn.text_factory = str
        return self.conn.cursor()

    def disconnect(self):
        self.conn.close()
        return

    def execute(self, query, values=None):
        cursor = self.connect()
        if values is None:
            cursor.execute(query)
        else:
            cursor.execute(query, values)

        self.conn.commit()
        return cursor


db = SqliteDb( dbname=dbname )


def exist_rid(table, rid, uri):
    global db
    sql_req = "SELECT COUNT(*) FROM %s WHERE id=? AND uri=?" % table
    cur = db.execute(sql_req, (rid,uri))
    res = cur.fetchone()[0]
    return res > 0


def insert_log(table, rid, req, uri):
    global db
    ts = int( time.time() )
    sql_req = "INSERT INTO %s VALUES (?, ?, ?, ?, ?)" % table
    db.execute(sql_req, (rid, req, uri, ts, ''))
    return


def update_log(table, rid, blob):
    sql_req = "SELECT * FROM %s WHERE id=?" % table
    cur = db.execute(sql_req, (rid,))
    new_blob = cur.fetchone()[1]
    new_blob+= blob

    if table == "requests":  sql_req = "UPDATE requests SET request=? WHERE id=?"
    else:                    sql_req = "UPDATE responses SET response=? WHERE id=?"
    db.execute(sql_req, (new_blob, rid))
    return


def proxenet_request_hook(request_id, request, uri):
    table = "requests"
    if exist_rid(table, request_id, uri):
        update_log(table, request_id, request)
    else:
        insert_log(table, request_id, request, uri)
    return request


def proxenet_response_hook(response_id, response, uri):
    table = "responses"
    if exist_rid(table, response_id, uri):
        update_log(table, response_id, response)
    else:
        insert_log(table, response_id, response, uri)
    return response


if __name__ == "__main__":
    uri = "foo"
    req = "GET / HTTP/1.1\r\nHost: foo\r\nX-Header: Powered by proxenet\r\n\r\n"
    res = "HTTP/1.0 200 OK\r\n\r\n"
    rid = 42
    proxenet_request_hook(rid, req, uri)
    proxenet_response_hook(rid, res, uri)
    db.disconnect()
    exit(0)
