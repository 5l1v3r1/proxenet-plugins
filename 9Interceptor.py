"""
Lightweight interception plugin for proxenet by @_hugsy_.
On top of simply intercepting the traffic, it can be
used to save request as raw text or as Python script
ready to replay.
Also it can be used to prepare `patator` command, for
file/argument fuzzing.

It will automatically recognize specific body formats:
* JSON
* XML

And also parse ASP.NET viewstate.

To add/remove file extensions to white/black list for
interception, edit the file from the CONFIG_FILE
variable.

Requires:
 - PyQt4
"""

import sys, os, urlparse, json, subprocess, inspect, copy
import socket, base64, pprint, urllib, ConfigParser

try:
    from lxml import etree
    from PyQt4 import QtCore, QtGui
    from PyQt4.QtGui import *
except ImportError as ie:
    print("Missing package: %s" % ie)
    exit(1)


PLUGIN_NAME = "Interceptor"
AUTHOR      = "hugsy"

CRLF = "\r\n"
CONFIG_FILE = os.getenv("HOME") + "/.proxenet.ini"
config = None

WINDOW_SIZE = (960, 600)


def error(msg):
    sys.stderr.write("\x1b[1m" + "\x1b[31m" + msg + "\x1b[0m\n")
    sys.stderr.flush()
    return


class DoNotInterceptException(Exception):
    pass


class OptionsView(QWidget):
    def __init__(self, parent):
        super(OptionsView, self).__init__()
        self.parent = parent
        self.setTabLayout()
        return

    def setTabLayout(self):
        lay = QVBoxLayout()
        l1 = QLabel()
        blacklist = config.get(PLUGIN_NAME, "__blacklisted")
        l1.setText("%d Blacklisted Extensions" % (len(blacklist)))
        te = QTextEdit()
        te.setFrameStyle(QFrame.Panel | QFrame.Plain)
        te.setDisabled(True)
        te.insertPlainText( "\n".join( blacklist ) )
        te.setFrameStyle(QFrame.Panel | QFrame.Plain)
        lay.addWidget(l1)
        lay.addWidget(te)
        self.setLayout(lay)
        return


class ViewState:
    def __init__(self, b64):
        self.vs_b64 = b64
        self.vs_raw = base64.decodestring(self.vs_b64)
        self.vs_arr = self.parseViewstate()
        return

    def decodeViewstate(self, i=0, p=[]):
        def decodeAsInt(j,q):
            n = str( ord(vs[j+1]) )
            q.append( ("<Int32>(%d)" % j, [(n, [])]), )
            return j+2

        def decodeAsString(j,t,q):
            l = ord(vs[j+1])
            s = vs[j+2 : j+2+l]
            q.append( ("<%s>(%d)"%(t,j), [(s, [])]), )
            return i+2+l

        def decodeAsArray(j,n,t,q):
            P = ("<%s>(%d)"%(t,j), )
            j = j+1
            for i in range(n):
                t = []
                j = self.decodeViewstate(j, t)
                P += (t,)
            q.append( P )
            return j

        vs = self.vs_raw[2:-20]

        if i >= len(vs):
            p.append( ("<End>(%d)"%i, []), )
            return

        if vs[i] == '\x02':
            return decodeAsInt(i,p)

        elif vs[i] == '\x05':
            return decodeAsString(i, "SystemString", p)

        elif vs[i] == '\x1e':
            return decodeAsString(i, "SystemWebUiIndexedString", p)

        elif vs[i] == '\x64':
            p.append( ("<Null>(%d)"%i, []), )
            return i+1

        elif vs[i] == '\x0f':
            return decodeAsArray(i,2,"Pair",p)

        elif vs[i] == '\x10':
            return decodeAsArray(i,3,"Triple",p)

        elif vs[i] == '\x16':
            return decodeAsArray(i+1,ord(vs[i+1]),"ArrayList",p)

        #
        # TODO: check those types
        #
        # \x07 Double
        # \x15 SystemStringArray
        # \x18 HybridDictionary
        # \x1f SystemWebUiIndexedChar
        # \x28 ClassType
        # \x32 SerializedClass
        # \x3c IndexedArray
        # \x66 IntZero
        # \x67 BooleanTrue
        # \x68 BooleanFalse
        #

        p.append( ("<Unknown-0x%x>(%d)"%(ord(vs[i]),i), []), )
        return i+1

    def parseViewstate(self):
        try:
            vs = []
            if not self.isValid(): raise Exception("ViewState is not a valid .NET ViewState")
            self.header = self.vs_raw[0:2]
            self.vs_hash = ":".join( [c.encode("hex") for c in self.vs_raw[-20:]] )
            self.decodeViewstate(i=0, p=vs)
        except Exception as e:
            print("VIEWSTATE decoding failed: %s" % e)
            vs = []

        return [ ("<ViewState>", vs),
                 ("<ViewStateHash>", [(self.vs_hash, [])]) ]

    def isValid(self):
        return self.vs_raw.startswith("\xff\x01")


class AspViewstateInterceptView(QWidget):
    def __init__(self, parent):
        super(AspViewstateInterceptView, self).__init__()
        self.parent = parent
        self.data = self.getViewState()
        if self.data is None:
            self.viewstate = []
        else:
            self.viewstate = ViewState(self.data)
        self.setTabLayout()
        return

    def getViewState(self):
        body = self.parent.parent.body
        args = [x.split("=") for x in body.split("&")]
        for k,v in args:
            if k=="__VIEWSTATE": return urllib.unquote(v)
        return None

    def setTabLayout(self):
        vs = self.viewstate.vs_arr
        lay = QVBoxLayout()
        l1 = QLabel("<b>ASP .NET</b> ViewState tree")
        m = QStandardItemModel()
        self.addItems(m, vs)
        self.tv = QTreeView()
        self.tv.setFrameStyle(QFrame.Panel | QFrame.Plain)
        self.tv.setModel(m)
        m.setHorizontalHeaderLabels(["ViewState"])
        lay.addWidget(l1)
        lay.addWidget(self.tv)
        self.setLayout(lay)
        return

    def addItems(self, model, elements):
        for elt in elements:
            text = elt[0]
            children = elt[1:]
            item = QStandardItem(text)
            model.appendRow(item)
            if len(children):
                for child in children:
                    self.addItems(item, child)
        return


class XmlInterceptView(QWidget):
    def __init__(self, parent):
        super(XmlInterceptView, self).__init__()
        self.parent = parent
        self.setTabLayout()
        return

    def setTabLayout(self):
        lay = QVBoxLayout()
        self.xmll = QLabel()
        self.xmlf = QTextEdit()
        self.xmlf.setFrameStyle(QFrame.Panel | QFrame.Plain)
        self.xmlf.textChanged.connect( self.updateFields )
        self.xmlf.insertPlainText( self.parent.parent.body )
        lay.addWidget(self.xmll)
        lay.addWidget(self.xmlf)
        self.setLayout(lay)
        return

    def updateFields(self):
        p = QPalette()

        try:
            body = str( self.xmlf.toPlainText() )
            parser = etree.XMLParser(dtd_validation=False)
            root = etree.fromstring( body, parser )
            p.setColor( QPalette.Foreground, QtCore.Qt.darkYellow )
            self.xmll.setText("Content is <b>valid</b> XML")
        except etree.XMLSyntaxError:
            p.setColor( QPalette.Foreground, QtCore.Qt.darkRed )
            self.xmll.setText("Content is <b>not valid</b> XML")
        except Exception as e:
            p.setColor( QPalette.Foreground, QtCore.Qt.darkRed )
            self.xmll.setText("Could not check XML validity: %s" % e)

        self.xmll.setPalette(p)
        self.parent.parent.body = body
        return

    def showEvent(self, event):
        body = self.parent.parent.body
        self.xmlf.clear()
        self.xmlf.insertPlainText( body )
        return


class JsonInterceptView(QWidget):
    def __init__(self, parent):
        super(JsonInterceptView, self).__init__()
        self.parent = parent
        self.setTabLayout()
        return

    def setTabLayout(self):
        lay = QVBoxLayout()
        self.jsonl = QLabel()
        self.jsonf = QTextEdit()
        self.jsonf.setFrameStyle(QFrame.Panel | QFrame.Plain)
        self.jsonf.textChanged.connect( self.updateFields )
        self.jsonf.insertPlainText( self.parent.parent.body )
        lay.addWidget(self.jsonl)
        lay.addWidget(self.jsonf)
        self.setLayout(lay)
        return

    def updateFields(self):
        p = QPalette()

        try:
            body = str( self.jsonf.toPlainText() )
            js = json.loads(body)
            p.setColor( QPalette.Foreground, QtCore.Qt.darkYellow )
            self.jsonl.setText("Content is <b>valid</b> JSON")
        except ValueError:
            p.setColor( QPalette.Foreground, QtCore.Qt.darkRed )
            self.jsonl.setText("Content is <b>not valid</b> JSON")

        self.jsonl.setPalette(p)
        self.parent.parent.body = body
        return

    def showEvent(self, event):
        body = self.parent.parent.body
        self.jsonf.clear()
        self.jsonf.insertPlainText( body )
        return


class RawInterceptView(QWidget):
    def __init__(self, parent):
        super(RawInterceptView, self).__init__()
        self.parent = parent
        self.setTabLayout()
        return

    def updateBody(self):
        self.parent.parent.body = self.rawBodyTextField.toPlainText()
        return

    def setTabLayout(self):
        self.rawBodyTextField = QTextEdit( self.parent.parent.body )
        self.rawBodyTextField.textChanged.connect( self.updateBody )
        self.rawBodyTextField.setFrameStyle(QFrame.Panel | QFrame.Plain)
        tabLayout = QVBoxLayout()
        tabLabel = QLabel("This frame displays the body content as <b>Raw</b>")
        tabLayout.addWidget(tabLabel)
        tabLayout.addWidget( self.rawBodyTextField )
        self.setLayout(tabLayout)
        return

    def showEvent(self, event):
        body = self.parent.parent.body
        self.rawBodyTextField.clear()
        self.rawBodyTextField.insertPlainText( body )
        return


class InterceptorMainWindow(QWidget):
    def __init__(self, parent):
        super(InterceptorMainWindow, self).__init__()
        self.parent = parent
        self.setTabs()
        self.setMainWindowLayout()
        return

    def updateHeaders(self):
        self.parent.headers = self.hdrEditField.toPlainText()

    def bounceRequest(self):
        body = str( self.parent.body )
        headers = self.updateContentLengthHeader() if self.do_updateClen else str(self.parent.headers)
        self.parent.data = "%s\n\n%s" % (headers, body)
        QApplication.quit()
        return

    def setTabs(self):
        self.tabs = QTabWidget()
        self.tabs.addTab( RawInterceptView(self), "Raw View" )
        self.tabs.addTab( JsonInterceptView(self), "JSON View" )
        self.tabs.addTab( XmlInterceptView(self), "XML View" )
        if "__VIEWSTATE=" in self.parent.body:
            self.tabs.addTab( AspViewstateInterceptView(self), "ViewState View" )

        self.tabs.addTab( OptionsView(self), "Options" )
        return

    def setMainWindowLayout(self):
        headerLayout = QVBoxLayout()
        lurl1 = QLabel("<b>URL</b>")
        lurl2 = QLabel("<p style=color:%s>%s</p>" % ("darkgreen" if self.parent.uri.startswith("https") \
                                                     else "darkred",
                                                     self.parent.uri))
        lheaders = QLabel("<b>Headers</b>")
        self.hdrEditField = QTextEdit()
        self.hdrEditField.insertPlainText( self.parent.headers )
        self.hdrEditField.setFrameStyle(QFrame.Panel | QFrame.Plain)
        self.hdrEditField.textChanged.connect(self.updateHeaders )
        headerLayout.addWidget(lurl1)
        headerLayout.addWidget(lurl2)
        headerLayout.addWidget(lheaders)
        headerLayout.addWidget(self.hdrEditField)

        bodyLayout = QVBoxLayout()
        l2 = QLabel("<b>Body</b>")
        bodyLayout.addWidget(l2)
        bodyLayout.addWidget( self.tabs )

        btnLayout = QHBoxLayout()
        btnLayout.addStretch(1)
        cb = QCheckBox("Update 'Content-Length' header")
        cb.stateChanged.connect(self.updateContentLengthState)
        cb.toggle()
        bounceButton = QPushButton("Bounce")
        bounceButton.clicked.connect(self.bounceRequest)
        cancelButton = QPushButton("Cancel")
        cancelButton.clicked.connect(QApplication.quit)
        btnLayout.addWidget(cb)
        btnLayout.addWidget(cancelButton)
        btnLayout.addWidget(bounceButton)

        vbox = QVBoxLayout()
        vbox.addLayout(headerLayout)
        vbox.addLayout(bodyLayout)
        vbox.addLayout(btnLayout)
        self.setLayout(vbox)
        return

    def updateContentLengthState(self, state):
        self.do_updateClen = (state == QtCore.Qt.Checked)
        return

    def updateContentLengthHeader(self):
        headers = str(self.parent.headers).split("\n")
        clen = len(self.parent.body)

        for i in xrange(len(headers)):
            head = str(headers[i])
            if head.startswith("Content-Length"):
                headers.pop(i)
                headers.append("Content-Length: %d" % clen)
                return "\n".join(headers)

        return "\n".join(headers)


class Interceptor(QMainWindow):

    def __init__(self, rid, uri, data):
        super(Interceptor, self).__init__()
        self.rid = rid
        self.uri = uri
        self.title = "Interceptor for proxenet: Request %d" % (rid,)
        self.data = data

        if config.has_option(PLUGIN_NAME, "blacklisted_extensions"):
            blacklist = config.get(PLUGIN_NAME, "blacklisted_extensions").split(" ")
        else:
            blacklist = []

        config.set(PLUGIN_NAME, "__blacklisted", blacklist)

        u = urlparse.urlparse(uri)
        if any( map(lambda x: u.path.endswith(x), blacklist) ):
            raise DoNotInterceptException()

        if not self.data.endswith("\n\n"):
            self.headers, self.body = self.data.split("\n\n")
        else:
            self.headers, self.body = self.data, ""

        self.intercept_shortcuts = { "SaveAsText"         : ("Ctrl+S", "Save As Text file", self.writeTxtFile),
                                     "SaveAsPython"       : ("Ctrl+P", "Save As Python script", self.writePyFile),
                                     "SaveAsRuby"         : ("Ctrl+R", "Save As Ruby script", self.writeRbFile),
                                     "SaveAsPerl"         : ("Ctrl+E", "Save As Perl script", self.writePlFile),
                                     "SaveAsCsrfPoc"      : ("Ctrl+O", "Create a CSRF PoC from current request", self.writeCsrfPoc),
                                     "ActionPatator"      : ("Ctrl+T", "Use 'patator' on the request", self.sendToPatator),
                                     "ActionSqlMap"       : ("Ctrl+I", "Use 'sqlmap' on the request", self.sendToSqlMap),
                                     "ActionHelp"         : ("Ctrl+H", "Show help", self.popupHelp),
        }

        self.setMainWindowProperty()
        self.setMainWindowMenuBar()
        self.setCentralWidget( InterceptorMainWindow( self ) )
        self.show()
        return

    def center(self):
        frameGm = self.frameGeometry()
        screen = QtGui.QApplication.desktop().screenNumber(QtGui.QApplication.desktop().cursor().pos())
        centerPoint = QtGui.QApplication.desktop().screenGeometry(screen).center()
        frameGm.moveCenter(centerPoint)
        self.move(frameGm.topLeft())
        return

    def setMainWindowProperty(self):
        self.resize(*WINDOW_SIZE)
        self.setFixedSize(*WINDOW_SIZE)
        self.setWindowTitle(self.title)
        self.center()

        if config.has_option(PLUGIN_NAME, "style"):
            qtlook = config.get(PLUGIN_NAME, "style")
        else:
            qtlook = "Cleanlooks"
        qApp.setStyle( qtlook )
        return

    def setMainWindowMenuBar(self):
        menubar = self.menuBar()
        fileMenu = menubar.addMenu('&Actions')
        for i in ["ActionHelp", "ActionSqlMap", "ActionPatator"]:
            code, desc, meth = self.intercept_shortcuts[i]
            action = QAction(QIcon(), desc, self)
            action.setShortcut(code)
            action.triggered.connect(meth)
            fileMenu.addAction(action)

        saveMenu = fileMenu.addMenu('Save As')
        for i in ["SaveAsText", "SaveAsPython", "SaveAsRuby", "SaveAsPerl", "SaveAsCsrfPoc"]:
            code, desc, meth = self.intercept_shortcuts[i]
            action = QAction(QIcon(), desc, self)
            action.setShortcut(code)
            action.triggered.connect(meth)
            saveMenu.addAction(action)
        return

    def popupHelp(self):
        title  = "Shortcut definition"
        desc   = "The following shortcuts are enabled:\n\n"
        for key in self.intercept_shortcuts.keys():
            sc, d = self.intercept_shortcuts[key][0],self.intercept_shortcuts[key][1]
            desc+= "* {}  {}\n".format(sc, d)
        QtGui.QMessageBox.information(self, title, desc, QtGui.QMessageBox.Information)
        return

    def sendToGeneric(self, cmd, cmd_text, **kwargs):
        clip = QApplication.clipboard()
        clip.setText(cmd_text)
        title = kwargs.get("title", "Send to %s" % cmd)
        desc = kwargs.get("desc", "'%s' command successfully copied to clipboard!" % cmd)
        QtGui.QMessageBox.information(self, title, desc, QtGui.QMessageBox.Ok)
        return

    def sendToSqlMap(self):
        method = self.headers.split(" ")[0].replace('"', '\\"')
        headers = self.headers.split("\n")
        cmd = """sqlmap.py -u "{}" """.format(self.uri)
        cmd+= """--method="{}" """.format(method.replace('"', '\\"'))
        for h in headers[1:]:
            if len(h):
                cmd+= """--header="{}" """.format(h.replace('"', '\\"'))
        if self.body is not None and len(self.body) > 0:
            cmd+= """--data="{}" """.format(self.body.replace('"', '\\"'))
        self.sendToGeneric("sqlmap", cmd)
        return

    def sendToPatator(self):
        method = self.headers.split(" ")[0].replace('"', '\\"')
        headers = self.headers.split("\n")

        cmd = """patator http_fuzz url="{}" 0=/path/to/wordlist.txt """.format(self.uri)
        cmd+= """method="{}" """.format(method.replace('"', '\\"'))
        for h in headers[1:]:
            if len(h):
                cmd+= """header="{}" """.format(h.replace('"', '\\"'))
        if self.body is not None and len(self.body) > 0:
            cmd+= """body="{}" """.format(self.body.replace('"', '\\"'))
        cmd+= "-x ignore:code=404 -x ignore,retry:code=500"

        self.sendToGeneric("patator", cmd)
        return

    def writeCsrfPoc(self):
        filename = QFileDialog().getSaveFileName(self, "Save CSRF PoC As", os.getenv("HOME"))
        if len(filename) == 0:
            return

        body = self.data.split("\n\n")[1]
        if len(body) == 0:
            return

        content = "<html><head><title>CSRF PoC</title></head><body>CSRF PoC for {}".format(self.uri)
        content+= "<form method='POST' action='{}'".format(self.uri)
        # content+= "enctype='multipart/form-data'" # todo
        content+= ">"
        for arg in body.split("&"):
            k,v = arg.split("=")
            content+= "<input type='hidden' name='{}' value='{}'>\n".format(k,v)

        content+= "<input type='Submit' value='Start CSRF PoC'>\n"
        content+= "</form></body></html>"

        with open(filename, "w") as f:
            f.write(content)

        QtGui.QMessageBox.information(self, "File written", "CSRF PoC written as '{}'".format(filename), QtGui.QMessageBox.Ok)
        return

    def writeGenericFile(self, title, content):
        filename = QFileDialog().getSaveFileName(self, title, os.getenv("HOME"))
        if len(filename) == 0:
            return
        with open(filename, "w") as f:
            f.write(content)
        return

    def writeTxtFile(self):
        self.writeGenericFile("Save Request as Text", self.data)
        return

    def writePyFile(self):
        o = urlparse.urlparse( self.uri )
        netloc = o.netloc.split(":")[0]
        if o.port is None: port = 443 if o.scheme == 'https' else 80
        else: port = int(o.port)
        data = self.data.replace("\n", "\\r\n")
        content = '''#!/usr/bin/env python
#
# Replay script for '{:s}'
#

import socket
{:s}

HOST = '{:s}'
PORT = {:d}

s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
{:s}
s.connect((HOST, PORT))
s.sendall(b"""{:s}""")

while True:
    data = s.recv(1024)
    print data
    if len(data)<=1024: break
s.close()
#
# Automatically generated by '{:s}'
#
'''.format(self.uri, "import ssl" if o.scheme=='https' else '', netloc,
           port, "s = ssl.wrap_socket(s)" if o.scheme=='https' else '',
           data, PLUGIN_NAME)

        self.writeGenericFile("Create Python script from Request", content)
        return

    def writeRbFile(self):
        o = urlparse.urlparse( self.uri )
        netloc = o.netloc.split(":")[0]
        if o.port is None: port = 443 if o.scheme == 'https' else 80
        else: port = int(o.port)
        data = self.data.replace("\n", "\r\n")
        content = '''#!/usr/bin/env ruby
#
# Replay script for '{:s}'
#
require 'socket'
{:s}

HOST = '{:s}'
PORT = {:d}

crlf = \"\\r\\n"
socktcp = TCPSocket.new(HOST, PORT)
'''.format(self.uri, "require 'openssl'" if o.scheme=='https' else '', netloc, port)
        if o.scheme=='https':
            content += '''sockssl = OpenSSL::SSL::SSLSocket.new(socktcp)
sockssl.connect()
sock = sockssl\n\n'''
        else:
            content += '''sock = socktcp\n\n'''

        content += "req = socktcp" + '\n'

        for line in self.data.split("\n"):
            content += "req << \"%s\" + crlf\n" % line

        content += '''sock.puts(req)

while line = sock.gets()
 puts line.chop()
end

sock.close()
#
# Automatically generated by '{:s}'
#
'''.format(PLUGIN_NAME)

        self.writeGenericFile("Create Ruby script from Request", content)
        return

    def writePlFile(self):
        o = urlparse.urlparse( self.uri )
        netloc = o.netloc.split(":")[0]
        if o.port is None: port = 443 if o.scheme == 'https' else 80
        else: port = int(o.port)
        data = self.data.replace("\n", "\\r\n")
        content = '''#!/usr/bin/env perl
#
# Replay script for '{:s}'
#
use IO::Socket::INET;
{:s}

$HOST = "{:s}";
$PORT = {:d};
$crlf = "\\r\\n";

'''.format(self.uri, "use IO::Socket::SSL;" if o.scheme=='https' else '', netloc, port)
        if o.scheme=='https':
            content += '''$sock = IO::Socket::SSL->new("$HOST:$PORT");'''
        else:
            content += '''$sock = new IO::Socket::INET(PeerHost=>"$HOST",PeerPort=>"$PORT",Proto=>'tcp');'''

        content += "\n"
        content += "$req  = \"\";\n"
        for line in self.data.split("\n"):
            content += "$req .= %s . $crlf;\n" % repr(line)
        content += """
print $sock $req;

while (my $line = <$sock>) {{
    print $line;
}}

close($sock);
#
# Automatically generated by '{:s}'
#
""".format(PLUGIN_NAME)

        self.writeGenericFile("Create Ruby script from Request", content)
        return

class ReceptorMainWindow(QWidget):
    def __init__(self, parent):
        super(ReceptorMainWindow, self).__init__()
        self.parent = parent
        self.do_updateClen = True
        self.setMainWindowLayout()
        return

    def bounceResponse(self):
        self.parent.body = str( self.bodyEditField.toPlainText() )
        if self.do_updateClen:
            self.updateContentLengthHeader()
        QApplication.quit()
        return

    def updateResponseBody(self):
        self.parent.body = self.bodyEditField.toPlainText()
        return

    def setMainWindowLayout(self):
        bodyLayout = QVBoxLayout()
        lurl1 = QLabel("<b>URL</b>")
        lurl2 = QLabel("<p style=color:%s>%s</p>" % ("darkgreen" if self.parent.uri.startswith("https") \
                                                     else "darkred",
                                                     self.parent.uri))
        lb = QLabel("<b>Body</b>")
        self.bodyEditField = QTextEdit()
        self.bodyEditField.insertPlainText( self.parent.body )
        self.bodyEditField.setFrameStyle(QFrame.Panel | QFrame.Plain)
        self.bodyEditField.textChanged.connect( self.updateResponseBody )
        bodyLayout.addWidget(lurl1)
        bodyLayout.addWidget(lurl2)
        bodyLayout.addWidget(lb)
        bodyLayout.addWidget(self.bodyEditField)

        btnLayout = QHBoxLayout()
        btnLayout.addStretch(1)
        cb = QCheckBox("Update 'Content-Length' header")
        cb.stateChanged.connect(self.updateContentLengthState)
        cb.toggle()
        bounceButton = QPushButton("Bounce")
        bounceButton.clicked.connect( self.bounceResponse )
        cancelButton = QPushButton("Cancel")
        cancelButton.clicked.connect(QApplication.quit)
        btnLayout.addWidget(cb)
        btnLayout.addWidget(cancelButton)
        btnLayout.addWidget(bounceButton)

        vbox = QVBoxLayout()
        vbox.addLayout(bodyLayout)
        vbox.addLayout(btnLayout)
        self.setLayout(vbox)
        return

    def updateContentLengthState(self, state):
        self.do_updateClen = (state == QtCore.Qt.Checked)
        return

    def updateContentLengthHeader(self):
        try:
            headers, body = self.parent.body.split("\n"*2)
            headers = headers.split("\n")
            content_length = len(body)
            done = False

            for i in xrange( len(headers) ):
                header = str( headers[i] )
                if header.startswith("Content-Length") and not done:
                    headers.pop(i)
                    headers.append("Content-Length: %d" % content_length)
                    done = True

            self.parent.body = "\n".join( headers ) + "\n"*2 + body
        except:
            pass
        return


class Receptor(QMainWindow):
    def __init__(self, rid, uri, data):
        super(Receptor, self).__init__()
        self.title = "Receptor for proxenet: Request-id %d" % (rid,)
        self.rid = rid
        self.uri = uri
        self.body = data

        self.setMainWindowProperty()
        self.setMainWindowMenuBar()
        self.setCentralWidget( ReceptorMainWindow( self ) )
        self.show()
        return

    def center(self):
        frameGm = self.frameGeometry()
        screen = QtGui.QApplication.desktop().screenNumber(QtGui.QApplication.desktop().cursor().pos())
        centerPoint = QtGui.QApplication.desktop().screenGeometry(screen).center()
        frameGm.moveCenter(centerPoint)
        self.move(frameGm.topLeft())
        return

    def setMainWindowProperty(self):
        self.resize(*WINDOW_SIZE)
        self.setFixedSize(*WINDOW_SIZE)
        self.setWindowTitle(self.title)
        self.center()

        if config.has_option(PLUGIN_NAME, "style"):
            qtlook = config.get(PLUGIN_NAME, "style")
        else:
            qtlook = "Cleanlooks"
        qApp.setStyle( qtlook )
        return

    def setMainWindowMenuBar(self):
        saveTxtFile = QAction(QIcon(), 'Save As Text file', self)
        saveTxtFile.setShortcut('Ctrl+T')
        saveTxtFile.triggered.connect(self.writeTxtFile)

        menubar = self.menuBar()
        fileMenu = menubar.addMenu('&Actions')
        fileMenu.addAction(saveTxtFile)
        return

    def writeTxtFile(self):
        filename = QFileDialog().getSaveFileName(self, "Save Request as Text", os.getenv("HOME"))
        if len(filename) == 0:
            return
        with open(filename, "w") as f:
            f.write(self.body)
        return


def is_blacklisted_extension(uri):
    global config

    if config.has_option(PLUGIN_NAME, "blacklisted_extensions"):
            blacklist = [x.lower() for x in config.get(PLUGIN_NAME, "blacklisted_extensions").split(" ")]
    else:
            blacklist = []

    o = urlparse.urlparse( uri )
    path = o.path.lower()
    for ext in blacklist:
        if path.endswith(ext):
            return True
    return False


def create_config_file():
    with open(CONFIG_FILE, "w") as f:
        f.write("[%s]\n" % PLUGIN_NAME)
        f.write("style = Cleanlooks\n")
        f.write("blacklisted_extensions = .css .js .jpg .png\n")
    return


def init_config():
    global config

    if config is None:
        if not os.access(CONFIG_FILE, os.R_OK):
            error("Creating config file at '%s'" % CONFIG_FILE)
            create_config_file()

        config = ConfigParser.ConfigParser()
        config.read(CONFIG_FILE)
    return


def intercept(rid, text, uri):
    init_config()
    if is_blacklisted_extension(uri):
        return text

    try:
        text = text.replace(CRLF, "\n")
        app = QApplication([uri,])
        win = Interceptor(rid, uri, text)
        win.show()
        app.exec_()
        ret = str(win.data).replace("\n", CRLF)
        return ret

    except Exception as e:
        error("An unexpected exception occured on request %d: %s" % (rid,e))
        return text


def recept(rid, text, uri):
    init_config()
    if is_blacklisted_extension(uri):
        return text

    try:
        text = text.replace(CRLF, "\n")
        app = QApplication([uri,])
        win = Receptor(rid, uri, text)
        win.show()
        app.exec_()
        ret = str(win.body).replace("\n", CRLF)
        return ret

    except Exception as e:
        error("An unexpected exception occured on response %d: %s" % (rid, e))
        return text


def call_gui(_type, rid, buffer, uri):
    cmd = ["python2", inspect.getfile(inspect.currentframe()), _type, str(rid), uri]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stdin=subprocess.PIPE)
    if p is None: return request
    data = p.communicate(input = buffer)[0]
    p.wait()
    return data


def proxenet_request_hook(request_id, request, uri):
    if __name__.endswith("InterceptorResponse") or __name__.endswith("Interceptor"):
        return call_gui("req", request_id, request, uri)
    else:
        return request


def proxenet_response_hook(response_id, response, uri):
    if __name__.endswith("InterceptorResponse") or __name__.endswith("Interceptor"):
        return call_gui("res", response_id, response, uri)
    else:
        return response


if __name__ == "__main__":
    if len(sys.argv) == 4:
        if sys.argv[1] in ("req", "res"):
            rid = int(sys.argv[2])
            buf = sys.stdin.read()
            url = sys.argv[3]
            if sys.argv[1] == "req":
                sys.stdout.write(intercept(rid, buf, url))
                sys.stdout.flush()
            elif sys.argv[1] == "res":
                sys.stdout.write(recept(rid, buf, url))
                sys.stdout.flush()
            exit(0)

    # test goes here
    rid = 1337
    vs = '%2fwEPDwUKMTQ2OTkzNDMyMWRkOWxNFeQcY9jzeKVCluHBdzA6WBo%3d'
    uri = "https://foo.bar/bar.asp"
    body = "&".join(["a=b", "b=c", "t=x", "__VIEWSTATE=%s"%vs])
    req = """POST /bar.asp HTTP/1.1\r
Host: foo.bar\r
X-Header: Powered by proxenet\r
Content-Length: %d\r
\r
%s""" % (len(body), body)

    os.write(2, '%d\n' % len(sys.argv))
    print ("="*50)
    print ("BEFORE:\n%s\n" % req)

    print ("="*50)
    print ("AFTER intercept:\n%s\n" % intercept(rid, req, uri))

    # print ("="*50)
    # print ("AFTER recept:\n%s\n" % recept(rid, req, uri))
