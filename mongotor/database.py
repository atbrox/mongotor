# coding: utf-8
# <mongotor - An asynchronous driver and toolkit for accessing MongoDB with Tornado>
# Copyright (C) <2012>  Marcel Nicolay <marcel.nicolay@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

from functools import partial
from datetime import timedelta
from tornado import gen
from tornado.ioloop import IOLoop
from bson import SON
from mongotor.node import Node, ReadPreference
from mongotor.errors import DatabaseError


class Database(object):
    """Database object
    """
    _instance = None

    def __new__(cls):
        if not cls._instance:
            cls._instance = super(Database, cls).__new__(cls)

        return cls._instance

    @gen.engine
    def init(self, addresses, dbname, **kwargs):
        self._addresses = self._parse_addresses(addresses)
        self._dbname = dbname
        self._connected = False
        self._nodes = []
        self._pool_kwargs = kwargs

        for host, port in self._addresses:
            node = Node(host, port, self, self._pool_kwargs)
            self._nodes.append(node)

        self._initialized = False

        ioloop_is_running = IOLoop.instance().running()
        self._config_nodes(callback=partial(self._on_config_node, ioloop_is_running))

        while True:
            if not ioloop_is_running:
                IOLoop.instance().start()

            if self._initialized:
                break

        IOLoop.instance().add_timeout(timedelta(seconds=10), self._config_nodes)

    def _on_config_node(self, ioloop_is_running):
        for node in self._nodes:
            if not node.initialized:
                return

        self._initialized = True
        if not ioloop_is_running:
            IOLoop.instance().stop()

    @property
    def dbname(self):
        return self._dbname

    def get_collection_name(self, collection):
        return u'%s.%s' % (self._dbname, collection)

    def _parse_addresses(self, addresses):
        if isinstance(addresses, (str, unicode)):
            addresses = [addresses]

        assert isinstance(addresses, list)

        parsed_addresses = []
        for address in addresses:
            host, port = address.split(":")
            parsed_addresses.append((host, int(port)))

        return parsed_addresses

    def _config_nodes(self, callback=None):

        for node in self._nodes:
            node.config(callback)

    @classmethod
    def connect(cls, addresses, dbname, **kwargs):
        """Connect to database

        :Parameters:
          - `addresses` :
          - `dbname` : database name
          - `kwargs` : kwargs passed to connection pool
        """
        if cls._instance:
            return cls._instance

        database = Database()
        database.init(addresses, dbname, **kwargs)

        return database

    @classmethod
    def disconnect(cls):
        if not cls._instance:
            raise ValueError("Database isn't connected")

        for node in cls._instance._nodes:
            node.disconnect()

        cls._instance = None

    @gen.engine
    def send_message(self, message, read_preference=ReadPreference.PRIMARY,
        callback=None):

        node = ReadPreference.select_node(self._nodes, read_preference)
        if not node:
            raise DatabaseError('could not find an available node')

        connection = yield gen.Task(node.pool.connection)
        try:
            connection.send_message(message, callback=callback)
        except:
            connection.close()
            raise

    def command(self, command, value=1, read_preference=ReadPreference.PRIMARY,
        callback=None, check=True, allowable_errors=[], **kwargs):
        """Issue a MongoDB command.

        Send command `command` to the database and return the
        response. If `command` is an instance of :class:`basestring`
        then the command {`command`: `value`} will be sent. Otherwise,
        `command` must be an instance of :class:`dict` and will be
        sent as is.

        Any additional keyword arguments will be added to the final
        command document before it is sent.

        For example, a command like ``{buildinfo: 1}`` can be sent
        using:

        >>> db.command("buildinfo")

        For a command where the value matters, like ``{collstats:
        collection_name}`` we can do:

        >>> db.command("collstats", collection_name)

        For commands that take additional arguments we can use
        kwargs. So ``{filemd5: object_id, root: file_root}`` becomes:

        >>> db.command("filemd5", object_id, root=file_root)

        :Parameters:
          - `command`: document representing the command to be issued,
            or the name of the command (for simple commands only).

            .. note:: the order of keys in the `command` document is
               significant (the "verb" must come first), so commands
               which require multiple keys (e.g. `findandmodify`)
               should use an instance of :class:`~bson.son.SON` or
               a string and kwargs instead of a Python `dict`.

          - `value` (optional): value to use for the command verb when
            `command` is passed as a string
          - `**kwargs` (optional): additional keyword arguments will
            be added to the command document before it is sent

        .. mongodoc:: commands
        """
        if isinstance(command, basestring):
            command = SON([(command, value)])

        command.update(kwargs)
        self._command(command, read_preference=read_preference, callback=callback)

    def _command(self, command, read_preference=ReadPreference.PRIMARY,
        connection=None, callback=None):

        from mongotor.cursor import Cursor
        cursor = Cursor('$cmd', command, is_command=True, connection=connection,
            read_preference=read_preference)

        cursor.find(limit=-1, callback=callback)
