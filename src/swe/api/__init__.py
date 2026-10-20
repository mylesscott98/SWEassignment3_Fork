from swe.api.app import create_app as create_base_app
from swe.api.execution import install_execution_routes


def create_app(dms=None):
    app = create_base_app(dms)
    install_execution_routes(app)
    return app
