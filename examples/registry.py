# -*- coding: utf-8 -*-
"""
Factornado registry example
---------------------------

You can run this example in typing:

>>> python registry.py &
[1] 15539

Then you can test it with:

>>> curl http://localhost:8800/hello

To end up the process, you can use:

>>> kill -SIGTERM -$(ps aux | grep 'python registry.py' | awk '{print $2}')
"""

import factornado
import os
import json
# import bson
from tornado import web, httputil, httpclient
import pandas as pd


class RegisterHandler(web.RequestHandler):
    """Register a new service."""

    def post(self, name=None):
        body = json.loads(self.request.body.decode('utf-8'))
        if 'url' not in body:
            raise web.HTTPError(500, 'body must at least contain `url`.')
        doc = {'id': pd.Timestamp.utcnow().value,
               '_id': body.pop('url'),
               'name': name,
               'info': body,
               }
        self.application.mongo.registry_collection.replace_one(
            {'_id': doc['_id']}, doc, upsert=True)
        self.write('ok')

    def get(self, name=None):
        query = {'name': name}
        confs = list(self.application.mongo.registry_collection.find(
                query,
                sort=[('id', -1)]))
        self.write({name: confs})


class GetAllHandler(web.RequestHandler):
    """Get the list of all registered services."""
    def get(self, param=''):
        data = pd.DataFrame([{'name': x['name'], 'doc': x}
                             for x in self.application.mongo.registry_collection.find()])
        self.write({name: group['doc'].tolist() for name, group in data.groupby('name')})


class ProxyHandler(web.RequestHandler):
    @web.asynchronous
    def redirection(self, method, name):
        # Get the service configuration.
        query = {'name': name}
        confs = list(self.application.mongo.registry_collection.find(
                query,
                sort=[('id', -1)]))  # We get the most recent heartbeat first.
        if len(confs) == 0:
            raise web.HTTPError(500, reason='Service {} not known.'.format(name))
        conf = confs[0]  # TODO: create round_robin here.

        url = conf.get('_id', None)
        if url is None:
            raise web.HTTPError(500, reason='Service {} has no url.'.format(name))
        user = conf.get('info', {}).get('user', None)
        password = conf.get('info', {}).get('password', None)

        # Parse the uri.
        uri = self.request.uri[1:]
        if not uri.startswith(name):
            raise web.HTTPError(
                500,
                reason='Uri {} does not start with {}.'.format(uri, name))
        uri = uri[len(name):]

        # Proxy the request.
        request = httpclient.HTTPRequest(
            url + uri,
            method=method,
            headers=httputil.HTTPHeaders({
                k: v for k, v in self.request.headers.get_all()
                if k.lower() != 'host'
                }),
            body=self.request.body,
            auth_username=user,
            auth_password=password,
            allow_nonstandard_methods=True,
            request_timeout=300.,
            validate_cert=False,
            )
        httpclient.AsyncHTTPClient().fetch(request, self.on_response)

    @web.asynchronous
    def get(self, name, uri=''):
        self.redirection('GET', name)

    @web.asynchronous
    def post(self, name, uri=''):
        self.redirection('POST', name)

    @web.asynchronous
    def put(self, name, uri=''):
        self.redirection('PUT', name)

    def on_response(self, response):
        if response.code == 304:
            self.set_status(304)
            self.finish()
            return

        if response.error is not None:
            raise web.HTTPError(response.code, reason=response.reason)

        self.set_status(response.code)

        for key, val in response.headers.get_all():
            if key not in ['Transfer-Encoding', 'Content-Encoding']:
                self.add_header(key, val)

        if response.body:
            self.write(response.body)
        self.finish()


class HelloHandler(web.RequestHandler):
    def get(self):
        self.write('This is registry\n')


config = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    'registry.yml')

app = factornado.Application(
    config,
    [
        ("[/]{0,1}", HelloHandler),
        ("/register/all", GetAllHandler),
        ("/register/([^/]*?)", RegisterHandler),
        ("/([^/]*?)/(.*)", ProxyHandler),
        ("/([^/]*?)", ProxyHandler),
        ])

if __name__ == "__main__":
    app.start_server()
