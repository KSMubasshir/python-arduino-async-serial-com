#!/usr/bin/python
from __future__ import print_function

import serial  # get from http://pyserial.sourceforge.net/
import sys
import time
import threading
import tornado.httpserver
import tornado.ioloop
import tornado.web
import tornado.websocket
import tornado.gen
from tornado.options import define, options
import json
from queue import Queue

define("port", default=8081, help="run on the given port", type=int)

clients = []

input_queue = Queue()
output_queue = Queue()


class IndexHandler(tornado.web.RequestHandler):
    def get(self):
        self.render('index.html')


class StaticFileHandler(tornado.web.RequestHandler):
    def get(self):
        self.render('static/main.js')


class GetHWPoller(threading.Thread):
    """ thread to repeatedly poll hardware
  sleeptime: time to sleep between pollfunc calls
  pollfunc: function to repeatedly call to poll hardware"""

    def __init__(self, sleeptime, pollfunc):

        self.sleeptime = sleeptime
        self.pollfunc = pollfunc
        threading.Thread.__init__(self)
        self.runflag = threading.Event()  # clear this to pause thread
        self.runflag.clear()
        # response is a byte string, not a string
        self.response = b''

    def run(self):
        self.runflag.set()
        self.worker()

    def worker(self):
        while True:
            if self.runflag.is_set():
                self.pollfunc()
                time.sleep(self.sleeptime)
            else:
                time.sleep(0.01)

    def pause(self):
        self.runflag.clear()

    def resume(self):
        self.runflag.set()

    def running(self):
        return self.runflag.is_set()


class HWInterface(object):
    """Class to interface with asynchronous serial hardware.
  Repeatedly polls hardware, unless we are sending a command
  "ser" is a serial port class from the serial module """

    def __init__(self, ser, sleeptime):
        self.ser = ser
        self.sleeptime = float(sleeptime)
        self.worker = GetHWPoller(self.sleeptime, self.poll_HW)
        self.worker.setDaemon(True)
        self.response = None  # last response retrieved by polling
        self.worker.start()
        self.callback = None
        self.verbose = True  # for debugging
        self.resp = ""

    def get_response(self):
        resp = self.resp
        self.resp = ""
        return resp

    def register_callback(self, proc):
        """Call this function when the hardware sends us serial data"""
        self.callback = proc

    def kill(self):
        self.worker.kill()

    def write_HW(self, command):
        """ Send a command to the hardware"""
        self.ser.write(command + b"\n")
        self.ser.flush()

    def poll_HW(self):
        """Called repeatedly by thread. Check for interlock, if OK read HW
    Stores response in self.response, returns a status code, "OK" if so"""

        response = self.ser.read(1)
        if response is not None:
            if len(response) > 0:  # did something write to us?
                response = response.strip()  # get rid of newline, whitespace
                if len(response) > 0:  # if an actual character
                    if self.verbose:
                        self.response = response
                        # print("poll response: " + self.response.decode("utf-8"))
                        self.resp = self.resp + self.response.decode("utf-8")
                        sys.stdout.flush()
                    if self.callback:
                        # a valid response so convert to string and call back
                        self.callback(self.response.decode("utf-8"))
                return "OK"
        return "None"  # got no response

portname = "COM10"
portbaud = "9600"
ser = serial.Serial(portname, portbaud, timeout=0, write_timeout=0)
hw = HWInterface(ser, 0.1)


def my_callback(response):
    """example callback function to use with HW_interface class.
     Called when the target sends a byte, just print it out"""
    # print('got HW response "%s"' % response)


class WebSocketHandler(tornado.websocket.WebSocketHandler):
    def open(self):
        print('new connection')
        clients.append(self)
        self.write_message("connected")

    def on_message(self, message):
        print('tornado received from client: %s' % json.dumps(message))
        # self.write_message('ack')
        input_queue.put(message)

    def on_close(self):
        print('connection closed')
        clients.remove(self)


# check the queue for pending messages, and rely on that to all connected clients
def check_queue():
    # print("Checking Queue")
    # print(input_queue.get())
    if not input_queue.empty():
        cmd = input_queue.get().split(' ')
        if cmd[0] == 'r':
            # print('Last response: "%s"' % hw.response)
            # output_queue.put(hw.response)
            # print('Last response: "%s"' % resp)
            output_queue.put(hw.get_response())
            

        elif cmd[0] == 's':
            val = bytes(cmd[1], 'utf-8')
            print("sending command " + val.decode('utf-8'))
            sys.stdout.flush()
            hw.write_HW(val)
            time.sleep(2)
            input_queue.put("r")
            # input_queue.put("r")

        elif cmd[0] == 'x':
            print("exiting...")
            exit()

        else:
            print("No such command: " + ' '.join(cmd))
    if not output_queue.empty():
        message = output_queue.get()
        print(message)
        for c in clients:
            c.write_message(message)


"""Designed to talk to Arduino running very simple demo program. 
Sending a serial byte to the Arduino controls the pin13 LED: ascii '1'
turns it on and ascii '0' turns it off. 

When the pin2 button state changes on the Arduino, it sends a byte:
ascii '1' for pressed, ascii '0' for released. """

if __name__ == '__main__':
    """ You can run this from the command line to test"""

    # Need to set the portname for your Arduino serial port:
    # see "Serial Port" entry in the Arduino "Tools" menu


    # timeout=0 means "non-blocking," ser.read() will always return, but
    # may return a null string.
    print("opened port " + portname + " at " + str(portbaud) + " baud")

    sys.stdout.flush()


    # when class gets data from the Arduino, it will call the my_callback function
    hw.register_callback(my_callback)

    tornado.options.parse_command_line()
    app = tornado.web.Application(
        handlers=[
            (r"/", IndexHandler),
            (r"/static/(.*)", tornado.web.StaticFileHandler, {'path': './'}),
            (r"/ws", WebSocketHandler)
        ]
    )
    httpServer = tornado.httpserver.HTTPServer(app)
    httpServer.listen(options.port)
    print("Listening on port:", options.port)
    print("Interactive mode. Commands:")
    print("  r to get last response byte")
    print("  s <str> to send string <str>")
    print("    's 1' turns on Arduino LED")
    print("    's 0' turns LED off")
    print("  x to exit ")

    mainLoop = tornado.ioloop.IOLoop.instance()
    # adjust the scheduler_interval according to the frames sent by the serial port
    scheduler_interval = 100
    scheduler = tornado.ioloop.PeriodicCallback(check_queue, scheduler_interval)
    scheduler.start()
    mainLoop.start()





