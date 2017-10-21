import os
import random

from chalice import Chalice
from chalice import NotFoundError, BadRequestError

from chalicelib import apigateway
from chalicelib import gh_oauth
from chalicelib import s3
from chalicelib import sdb
from chalicelib import settings


app = Chalice(app_name=settings.APP_NAME)
app.debug = settings.DEBUG


@app.route('/')
def index():
    return 'GROT2 server'


@app.route('/gh-oauth')
def gh_oauth_endpoint():
    gh_code = app.current_request.query_params.get('code', None)
    if not gh_code:
        raise BadRequestError('no code')

    user_data = gh_oauth.get_user_data(
        gh_code,
        app.current_request.stage_vars.get(
            'GH_OAUTH_CLIENT_ID', os.environ.get('GH_OAUTH_CLIENT_ID')),
        app.current_request.stage_vars.get(
            'GH_OAUTH_CLIENT_SECRET', os.environ.get('GH_OAUTH_CLIENT_SECRET')),
    )
    if not user_data:
        raise BadRequestError('invalid code')

    user_id = user_data['login']
    email = user_data.get('email', '')
    api_key = apigateway.get_api_key(user_id)
    if not api_key:
        api_key = apigateway.new_api_key(user_id, email)
        sdb.new_user(user_id, email, api_key)
    return {'x-api-key': api_key}


@app.route('/match', methods=['PUT'], api_key_required=True)
def match_put():
    api_key = app.current_request.context['identity']['apiKey']
    user_id = sdb.get_user_id(api_key)
    match_id = sdb.new_match(api_key, user_id)
    sdb.increment_total_matches(user_id)
    s3.update_hof(sdb.get_hof_data())
    return {'match_id': match_id}


@app.route('/match/{match_id}', methods=['GET'], api_key_required=True)
def match_get(match_id):
    api_key = app.current_request.context['identity']['apiKey']
    match = sdb.get_match(api_key, match_id)
    if not match:
        raise BadRequestError('invalid match_id')
    return match.get_state()


@app.route('/match/{match_id}', methods=['POST'], api_key_required=True)
def match_post(match_id):
    api_key = app.current_request.context['identity']['apiKey']
    match = sdb.get_match(api_key, match_id)
    if not match.is_active():
        return match.get_state()

    request = app.current_request
    data = request.json_body
    try:
        x = int(data['x'])
        y = int(data['y'])
    except (
        KeyError,
        TypeError,
        ValueError,
    ):
        raise BadRequestError('x or y is missing or not int')

    if not 0 <= x < settings.BOARD_SIZE or not 0 <= y < settings.BOARD_SIZE:
        raise BadRequestError('x or y is out of range')

    match.start_move(x, y)
    sdb.update_match(api_key, match_id, match)
    if not match.is_active():
        sdb.increment_total_score(match.player.user_id, match.score)
        s3.update_hof(sdb.get_hof_data())

    return match.get_state()


@app.route('/match/{match_id}/results', methods=['GET'])
def match_results_get(match_id):
    matches = sdb.get_match_results(match_id)
    matches.sort(key=lambda i: int(i['score']), reverse=True)
    return {
        'players': [
            {'user': match['user_id'], 'score': match['score']}
            for match in matches
        ]
    }