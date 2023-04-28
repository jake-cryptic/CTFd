import base64
import json
import tempfile
import traceback

from flask import Blueprint, render_template, request, jsonify

from CTFd.api import CTFd_API_v1
from CTFd.models import Teams, Users, db
from CTFd.plugins import register_plugin_assets_directory
from CTFd.plugins.challenges import CHALLENGE_CLASSES
from CTFd.utils.config import is_teams_mode
from CTFd.utils.decorators import admins_only

from .api import (active_docker_namespace, container_namespace,
                  docker_namespace, kill_container, secret_namespace)
from .functions.general import get_repositories, get_docker_info, do_request
from .models.container import DockerChallengeType
from .models.models import (DockerChallengeTracker, DockerConfig,
                            DockerConfigForm, DockerRegistry)
from .models.service import DockerServiceChallengeType


def __handle_file_upload(file_key, b_obj, attr_name):
    if file_key not in request.files:
        setattr(b_obj, attr_name, '')
        return

    try:
        file_content = request.files[file_key].stream.read()
        if len(file_content) != 0:
            tmp_file = tempfile.NamedTemporaryFile(mode="wb", dir="/tmp", delete=False)
            tmp_file.write(file_content)
            tmp_file.seek(0)
            setattr(b_obj, attr_name, tmp_file.name)
            return
    except Exception as err:
        print(err)

    setattr(b_obj, attr_name, '')


def define_docker_admin(app):
    admin_docker_config = Blueprint('admin_docker_config', __name__, template_folder='templates',
                                    static_folder='assets')

    @admin_docker_config.route("/admin/docker_config", methods=["GET", "POST"])
    @admins_only
    def docker_config():
        docker = DockerConfig.query.filter_by(id=1).first()
        if docker:
            b = docker
        else:
            print('No docker config was found, setting empty one.')
            b = DockerConfig()
            db.session.add(b)
            db.session.commit()
            docker = DockerConfig.query.filter_by(id=1).first()

        form = DockerConfigForm()
        if request.method == "POST":

            __handle_file_upload('ca_cert', b, 'ca_cert')
            __handle_file_upload('client_cert', b, 'client_cert')
            __handle_file_upload('client_key', b, 'client_key')

            b.hostname = request.form['hostname']

            b.tls_enabled = False
            if 'tls_enabled' in request.form:
                b.tls_enabled = request.form['tls_enabled'] == "True"

            if not b.tls_enabled:
                b.ca_cert = None
                b.client_cert = None
                b.client_key = None

            repositories = request.form.to_dict(flat=False).get('repositories', None)
            if repositories:
                b.repositories = ','.join(repositories)
            else:
                b.repositories = None

            db.session.add(b)
            db.session.commit()
            docker = DockerConfig.query.filter_by(id=1).first()

        try:
            repos = get_repositories(docker)
        except:
            print(traceback.print_exc())
            repos = list()

        if len(repos) == 0:
            form.repositories.choices = [("ERROR", "Failed to load repositories")]
        else:
            form.repositories.choices = [(d, d) for d in repos]

        dconfig = DockerConfig.query.first()
        try:
            selected_repos = dconfig.repositories
            if selected_repos == None:
                selected_repos = list()
        except:
            print(traceback.print_exc())
            selected_repos = []

        dinfo = get_docker_info(docker)

        return render_template("docker_config.html", config=dconfig, form=form, repos=selected_repos, info=dinfo)

    @admin_docker_config.route("/admin/docker_config/pull_image", methods=["GET"])
    @admins_only
    def pull_image():
        docker = DockerConfig.query.filter_by(id=1).first()

        if not docker:
            return jsonify({"error": "Docker configuration not found"}), 404

        image = request.args.get('image', None)

        if not image:
            return jsonify({"error": "No image specified"}), 400

        registry = 'registry.hub.docker.com'
        if '/' in image:
            registry = image.split('/')[0]

        registry_auth = DockerRegistry.query.filter_by(url=registry).first()
        if not registry_auth:
            return jsonify({"error": f"No registry authentication found in DB for: {registry}"}), 403

        try:
            auth_token = base64.b64encode(f"{registry_auth.username}:{registry_auth.password}".encode("utf-8")).decode("utf-8")
            response = do_request(docker, f"/images/create?fromImage={image}",
                                  headers={"X-Registry-Auth": auth_token}, method="POST")

            if response.status_code == 200:
                return jsonify({"message": "Image pulled successfully"}), 200
            else:
                return jsonify({"error": f"Error pulling image: {response.text}"}), 500

        except Exception as e:
            return jsonify({"error": f"Error pulling image: {str(e)}"}), 500

    @admin_docker_config.route("/admin/docker_config/login", methods=["GET", "POST"])
    def docker_login():
        docker = DockerConfig.query.filter_by(id=1).first()

        if not docker:
            return jsonify({"error": "Docker configuration not found"}), 404

        username = request.args.get('username', None)
        password = request.args.get('password', None)
        registry = request.args.get('registry', None)

        if not username or not password:
            return jsonify({"error": "Username and password are required"}), 400

        auth_data = {
            "username": username,
            "password": password,
            "serveraddress": registry
        }

        try:
            response = do_request(docker, f"/auth?registry={registry}",
                                  headers={"Content-Type": "application/json"},
                                  method="POST",
                                  data=json.dumps(auth_data))

            if response.status_code == 200:
                registry_entry = DockerRegistry.query.filter_by(url=registry).first()
                if not registry_entry:
                    registry_entry = DockerRegistry(url=registry, username=username, password=password)
                    db.session.add(registry_entry)
                else:
                    registry_entry.username = username
                    registry_entry.password = password

                db.session.commit()
                return jsonify({"message": "Docker login successful"}), 200
            else:
                return jsonify({"error": f"Error during login: {response.text}"}), 500

        except Exception as e:
            return jsonify({"error": f"Error during login: {str(e)}"}), 500

    @admin_docker_config.route("/admin/docker_config/logged_in_registries", methods=["GET"])
    @admins_only
    def get_logged_in_registries():
        registries = DockerRegistry.query.all()
        registry_list = [f"{registry.username}@{registry.url}\n" for registry in registries]
        return jsonify({"message": registry_list}), 200

    app.register_blueprint(admin_docker_config)


def define_docker_status(app):
    admin_docker_status = Blueprint('admin_docker_status', __name__, template_folder='templates',
                                    static_folder='assets')

    @admin_docker_status.route("/admin/docker_status", methods=["GET", "POST"])
    @admins_only
    def docker_admin():
        try:
            docker_tracker = DockerChallengeTracker.query.all()
            for i in docker_tracker:
                if is_teams_mode():
                    name = Teams.query.filter_by(id=i.team_id).first()
                    i.team_id = name.name
                else:
                    name = Users.query.filter_by(id=i.user_id).first()
                    i.user_id = name.name
        except Exception as err:
            return render_template("admin_docker_status.html", dockers=[], error_msg=err)

        return render_template("admin_docker_status.html", dockers=docker_tracker)

    app.register_blueprint(admin_docker_status)


def load(app):
    app.db.create_all()

    CHALLENGE_CLASSES['docker'] = DockerChallengeType
    CHALLENGE_CLASSES['docker_service'] = DockerServiceChallengeType

    register_plugin_assets_directory(app, base_path='/plugins/docker_challenges/assets')

    define_docker_admin(app)
    define_docker_status(app)

    CTFd_API_v1.add_namespace(docker_namespace, '/docker')
    CTFd_API_v1.add_namespace(container_namespace, '/container')
    CTFd_API_v1.add_namespace(active_docker_namespace, '/docker_status')
    CTFd_API_v1.add_namespace(kill_container, '/nuke')
    CTFd_API_v1.add_namespace(secret_namespace, '/secret')
