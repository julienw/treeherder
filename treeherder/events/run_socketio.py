#!/usr/bin/env python
import sys
import argparse
from os.path import dirname
import gevent
from gevent import monkey
monkey.patch_all()
from socketio.server import SocketIOServer
from socketio import socketio_manage
from kombu import Connection
import logging

sys.path.append(dirname(dirname(dirname(__file__))))

from treeherder.events.consumer import EventsConsumer
from treeherder.events.sockets import EventsNamespace

logger = logging.getLogger("treeherder.events")

class Application(object):
    """wsgi application with socketio enabled"""

    def __init__(self):
        self.buffer = []

    def __call__(self, environ, start_response):
        path = environ['PATH_INFO'].strip('/') or 'index.html'

        if path.startswith("socket.io"):
            socketio_manage(environ, {'/events': EventsNamespace})
        else:
            return not_found(start_response)


def not_found(start_response):
    start_response('404 Not Found', [])
    return ['<h1>Not Found</h1>']


def broadcast_subscribers(body, msg):
    """
    This is the main function where all the magic happens
    It broadcasts the events to the clients subscribed to
    them.
    """
    pkt = dict(type="event", name=body['event'],
               args=body, endpoint='/events')

    logger.error("emitting event {0} on branch {1}".format(
        body["event"], body["branch"]
    ))

    for session_id, socket in server.sockets.iteritems():
        # loop over all the open connections
        # and send a message when needed
        if "subscriptions" not in socket.session:
            continue

        for branch, events in socket.session['subscriptions'].items():
            if branch == body["branch"] or branch == "*":
                if body["event"] in events or "*" in events:
                    logger.debug("sending packet {0} to {1}".format(
                        pkt, session_id
                    ))
                    socket.send_packet(pkt)
                    break
    msg.ack()


def start_consumer(broker_url):
    with Connection(broker_url) as conn:
        consumer = EventsConsumer(conn)
        consumer.listen_to("events.#", broadcast_subscribers)
        consumer.run()


if __name__ == "__main__":

    parser = argparse.ArgumentParser()
    parser.add_argument("--host",
                        help="interface to bind the server to",
                        default="0.0.0.0")
    parser.add_argument("--port",
                        help="port to bind the server to",
                        default="8005",
                        type=int)
    parser.add_argument("--broker-url",
                        help="url of the broker to use",
                        required=True)
    parser.add_argument('--log-file',
                        default="stdout",
                        help="""the file where the log should be written to.
Default to stdout""")
    parser.add_argument("--log-level",
                        help="minimum level to log",
                        default="DEBUG",
                        choices=["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"])
    args = parser.parse_args()

    # logging system setup
    logger.setLevel(args.log_level)

    if not args.log_file:
        args.log_file = "stdout"
    if args.log_file in ["stdout", "stderr"]:
        log_handler = logging.StreamHandler(getattr(sys, args.log_file))
    else:
        log_handler = logging.FileHandler(args.log_file)
    log_handler.setLevel(args.log_level)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    log_handler.setFormatter(formatter)
    logger.addHandler(log_handler)

    try:
        logger.info("Starting SocketIOServer")

        server = SocketIOServer((args.host, args.port), Application(),
                                resource="socket.io", policy_server=False)
        logger.info("Listening to http://{0}:{1}".format(args.host, args.port))
        logger.debug("writing logs to %s" % args.log_file)
        gevent.spawn(start_consumer, args.broker_url)
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Socketio server stopped")
        for handler in logger.handlers:
            try:
                handler.close()
            except AttributeError:
                pass
