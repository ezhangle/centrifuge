# coding: utf-8
#
# Copyright (c) Alexandr Emelin. BSD license.
# All rights reserved.

import tornado.web
from tornado.escape import json_encode
from tornado.gen import coroutine
from sockjs.tornado import SockJSConnection

from jsonschema import validate, ValidationError

from . import auth
from .core import Response
from .client import Client
from .schema import req_schema, admin_params_schema


class BaseHandler(tornado.web.RequestHandler):

    def get_current_user(self):
        user = self.get_secure_cookie("user")
        if not user:
            return None
        return user

    def json_response(self, to_return):
        """
        Finish asynchronous request and return JSON response.
        """
        self.set_header('Content-Type', 'application/json; charset=utf-8')
        self.finish(tornado.escape.json_encode(to_return))

    @property
    def opts(self):
        return self.settings.get('config', {})


class ApiHandler(BaseHandler):
    """
    Listen for incoming POST request, authorize it and in case off
    successful authorization process requested action for project.
    """
    def check_xsrf_cookie(self):
        """
        No need in CSRF protection here.
        """
        pass

    @coroutine
    def post(self, project_id):
        """
        Handle API HTTP requests.
        """
        if not self.request.body:
            raise tornado.web.HTTPError(400, log_message="empty request")

        auth_info, error = auth.extract_auth_info(self.request)
        if error:
            raise tornado.web.HTTPError(401, log_message=error)

        sign = auth_info.get('sign')
        if not sign:
            raise tornado.web.HTTPError(401, log_message="unauthorized")

        project, error = yield self.application.structure.get_project_by_id(project_id)
        if error:
            raise tornado.web.HTTPError(500, log_message=str(error))
        if not project:
            raise tornado.web.HTTPError(404, log_message="project not found")

        encoded_data = self.request.body
        if not encoded_data:
            raise tornado.web.HTTPError(400, log_message="no request body")

        result, error = yield self.application.structure.check_auth(project, sign, encoded_data)
        if error:
            raise tornado.web.HTTPError(500, log_message=str(error))
        if not result:
            raise tornado.web.HTTPError(401, log_message="unauthorized")

        data = auth.decode_data(encoded_data)
        if not data:
            raise tornado.web.HTTPError(400, log_message="malformed data")

        response = Response()

        try:
            validate(data, req_schema)
        except ValidationError as e:
            response.error = str(e)
        else:
            req_id = data.get("uid", None)
            method = data.get("method")
            params = data.get("params")

            response.uid = req_id
            response.method = method

            if method not in admin_params_schema:
                response.error = "method not found"
            else:
                try:
                    validate(params, admin_params_schema[method])
                except ValidationError as e:
                    response.error = str(e)
                else:
                    result, error = yield self.application.process_call(
                        project, method, params
                    )
                    response.body = result
                    response.error = error

        self.json_response(response.as_message())


class SockjsConnection(SockJSConnection):

    def on_open(self, info):
        self.client = Client(self, info)
        if self.session:
            if self.session.transport_name != 'rawwebsocket':
                self.session.start_heartbeat()
        else:
            self.close()

    def on_message(self, message):
        self.client.message_received(message)

    def on_close(self):
        self.client.close()
        del self.client
