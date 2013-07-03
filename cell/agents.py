"""cell.agents"""

from __future__ import absolute_import

from inspect import isclass

from kombu.common import uuid, ignore_errors
from kombu.log import get_logger, setup_logging
from kombu.mixins import ConsumerMixin
from kombu.utils import symbol_by_name

from .actors import Actor, ActorProxy, ACTOR_TYPE
from .utils import qualname

__all__ = ['Agent', 'dAgent']

logger = get_logger(__name__)
debug, warn, error = logger.debug, logger.warn, logger.error


class dAgent(Actor):
    types = (ACTOR_TYPE.RR, ACTOR_TYPE.SCATTER)

    class state(object):

        def _start_actor_consumer(self, actor):
            actor.consumer = actor.Consumer(self.connection.channel())
            actor.consumer.consume()
            self.actor.registry[actor.id] = actor

        def add_actor(self, name, id=None):
            """Add actor to the registry and start the actor's main method."""
            try:
                actor = symbol_by_name(name)(
                    connection=self.connection, id=id, agent=self,
                )
                if actor.id in self.actor.registry:
                    warn('Actor id %r already exists', actor.id)
                self._start_actor_consumer(actor)
                debug('Actor registered: %s', name)
                return actor.id
            except Exception as exc:
                error('Cannot start actor: %r', exc, exc_info=True)

        def stop_all(self):
            self.actor.shutdown()

        def reset(self):
            debug('Resetting active actors')
            for actor in self.actor.registry.itervalues():
                if actor.consumer:
                    ignore_errors(self.connection, actor.consumer.cancel)
                actor.connection = self.connection
                self._start_actor_consumer(actor)

        def stop_actor(self, id):
            try:
                actor = self.actor.registry.pop(id)
            except KeyError:
                pass
            else:
                if actor.consumer and actor.consumer.channel:
                    ignore_errors(self.connection, actor.consumer.cancel)

    def __init__(self, connection, id=None):
        self.registry = {}
        Actor.__init__(self, connection=connection, id=id, agent=self)

    def contribute_to_state(self, state):
        state = super(dAgent, self).contribute_to_state(state)
        state.connection = self.connection
        state.connection_errors = self.connection.connection_errors
        state.channel_errors = self.connection.channel_errors
        state.reset()

    def add_actor(self, actor, nowait=False):
        name = qualname(actor)
        actor_id = uuid()
        res = self.call('add_actor', {'name': name, 'id': actor_id},
                        type='round-robin', nowait=True)
        actor_proxy = ActorProxy(actor, actor_id, res)
        return actor_proxy

    def stop_actor_by_id(self, actor_id, nowait=False):
        return self.scatter('stop_actor', {'actor_id': actor_id},
                            nowait=nowait)

    def start(self):
        debug('Starting Agent')

    def _shutdown(self, cancel=True, close=True, clear=True):
        try:
            for actor in self.registry.itervalues():
                if actor and actor.consumer:
                    if cancel:
                        ignore_errors(self.connection, actor.consumer.cancel)
                    if close and actor.consumer.channel:
                        ignore_errors(self.connection,
                                      actor.consumer.channel.close)
        finally:
            if clear:
                self.registry.clear()

    def stop(self):
        self._shutdown(clear=False)

    def shutdown(self):
        self._shutdown(cancel=False)

    def get_default_scatter_limit(self, actor):
        return None


class Agent(ConsumerMixin):
    actors = []

    def __init__(self, connection, id=None, actors=None):
        self.connection = connection
        self.id = id or uuid()
        if actors is not None:
            self.actors = actors
        self.actors = self.prepare_actors()

    def on_run(self):
        pass

    def run(self):
        self.info('Agent on behalf of [%s] starting...',
                  ', '.join(actor.name for actor in self.actors))
        self.on_run()
        super(Agent, self).run()

    def stop(self):
        pass

    def on_consume_ready(self, *args, **kwargs):
        for actor in self.actors:
            actor.on_agent_ready()

    def run_from_commandline(self, loglevel='INFO', logfile=None):
        setup_logging(loglevel, logfile)
        try:
            self.run()
        except KeyboardInterrupt:
            self.info('[Quit requested by user]')

    def _maybe_actor(self, actor):
        if isclass(actor):
            return actor(self.connection)
        return actor

    def prepare_actors(self):
        return [self._maybe_actor(actor).bind(self.connection, self)
                    for actor in self.actors]

    def get_consumers(self, Consumer, channel):
        return [actor.Consumer(channel) for actor in self.actors]

    def get_default_scatter_limit(self, actor):
        return None
