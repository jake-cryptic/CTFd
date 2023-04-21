import os

from flask import Blueprint, render_template_string
from CTFd.models import db, Challenges
from CTFd.plugins import register_plugin_assets_directory
from CTFd.utils.decorators import during_ctf_time_only
from CTFd.utils.user import is_admin, get_current_user

class ChatID(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    challenge_id = db.Column(db.Integer, db.ForeignKey("challenges.id", ondelete="CASCADE"))
    chat_id = db.Column(db.String(255), unique=True)

    def __init__(self, challenge_id, chat_id):
        self.challenge_id = challenge_id
        self.chat_id = chat_id


def chat_plugin(challenge_id):
    template_path = os.path.join(os.path.dirname(os.path.realpath(__file__)), "templates/challenges/chat_iframe.html")
    template_content = open(template_path).read()

    chat_id_obj = ChatID.query.filter_by(challenge_id=challenge_id).first()
    chat_id = chat_id_obj.chat_id if chat_id_obj else ""
    return render_template_string(template_content, chat_id=chat_id)


def load(app):
    # Set up the Blueprint
    blueprint = Blueprint("chat_plugin", __name__, template_folder="templates", static_folder="assets")
    app.register_blueprint(blueprint)

    # Register assets directory
    register_plugin_assets_directory(app, base_path="/plugins/chat_plugin/assets/")

    # Create the chat_id table
    db.create_all()

    app.jinja_env.globals.update(chat_plugin=chat_plugin)
