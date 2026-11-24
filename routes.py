from flask import render_template, request, session, jsonify, redirect
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import pyotp
from app import (
    app, get_user, create_user, get_player,
    add_player_to_queue_if_not_exists, make_qr_base64, empty_player_block,
    add_user_player, get_user_players, remove_user_player, is_player_in_queue, get_player_history
)


@app.route('/players', methods=['GET'])
def players_page():
    if not session.get('logged_in'):
        return redirect('/')
    return render_template('players.html')


@app.route('/api/my_players', methods=['GET'])
def api_my_players():
    if not session.get('logged_in'):
        return jsonify({'error': 'no auth'}), 401
    return jsonify({'players': get_user_players(session['user_id'])})


@app.route('/api/my_players', methods=['POST'])
def api_add_my_player():
    if not session.get('logged_in'):
        return jsonify({'error': 'no auth'}), 401
    data = request.get_json(silent=True) or {}
    nick = data.get('nickname', '').strip()
    reg = data.get('region', 'RU').strip().upper()
    if not nick:
        return jsonify({'error': 'nickname required'}), 400
    add_user_player(session['user_id'], nick, reg)
    add_player_to_queue_if_not_exists(nick, reg)
    return jsonify({'ok': True})


@app.route('/api/my_players', methods=['DELETE'])
def api_remove_my_player():
    if not session.get('logged_in'):
        return jsonify({'error': 'no auth'}), 401
    data = request.get_json(silent=True) or {}
    nick = data.get('nickname', '').strip()
    reg = data.get('region', 'RU').strip().upper()
    remove_user_player(session['user_id'], nick, reg)
    return jsonify({'ok': True})

@app.route('/api/player/history', methods=['GET'])
def api_player_history():
    if not session.get('logged_in'):
        return jsonify({'error': 'no auth'}), 401
    nick = request.args.get('nickname', '').strip()
    reg = request.args.get('region', 'RU').strip().upper()
    if not nick:
        return jsonify({'history': []})
    return jsonify({'history': get_player_history(nick, reg)})

@app.route('/api/player', methods=['GET'])
def api_player():
    if not session.get('logged_in'):
        return jsonify({'error': 'no auth'}), 401

    nickname = request.args.get('nickname', '').strip()
    region = request.args.get('region', 'RU').strip().upper()

    if not nickname:
        return jsonify(empty_player_block())

    row = get_player(nickname, region)
    
    # Если записи в базе НЕТ
    if row is None:
        # Проверяем, есть ли он в очереди (значит, воркер ещё не дошел до него)
        in_queue = is_player_in_queue(nickname, region)
        block = empty_player_block()
        block['nickname'] = nickname
        block['region'] = region
        block['status'] = 'pending' if in_queue else 'not_found'
        return jsonify(block)

    # Если запись ЕСТЬ
    last_online = row['last_online'] or 'не найден'
    # Если воркер записал "не найден", значит игрока нет. Иначе - найден.
    status = 'found' if last_online != 'не найден' else 'not_found'
    
    # Если воркер записал нули и "не найден", но updated_at пустой (старые записи)
    if not row['updated_at']:
        status = 'pending'

    kd_value = row['kd'] if row['kd'] is not None else 0

    return jsonify({
        'nickname': row['nickname'],
        'region': row['region'],
        'kills': row['kills'] if row['kills'] is not None else 0,
        'deaths': row['deaths'] if row['deaths'] is not None else 0,
        'hours_played': row['hours_played'] if row['hours_played'] is not None else 0,
        'kd': round(kd_value, 2),
        'last_online': last_online,
        'updated_at': row['updated_at'] or '-',
        'status': status
    })

from app import (
    app,
    get_user,
    create_user,
    get_player,
    add_player_to_queue_if_not_exists,
    make_qr_base64,
    empty_player_block
)


@app.route('/', methods=['GET'])
def index():
    if session.get('logged_in'):
        return render_template(
            'index.html',
            logged_in=True,
            show_2fa=False,
            qr_code=None,
            secret=None,
            message=''
        )

    if session.get('pre_2fa_login'):
        return render_template(
            'index.html',
            logged_in=False,
            show_2fa=True,
            qr_code=None,
            secret=None,
            message=''
        )

    return render_template(
        'index.html',
        logged_in=False,
        show_2fa=False,
        qr_code=None,
        secret=None,
        message=''
    )


@app.route('/register', methods=['POST'])
def register():
    login = request.form.get('register_login', '').strip()
    password = request.form.get('register_password', '')
    password2 = request.form.get('register_password2', '')

    if not login or not password or not password2:
        return render_template(
            'index.html',
            logged_in=False,
            show_2fa=False,
            qr_code=None,
            secret=None,
            message='заполните все поля'
        )

    if password != password2:
        return render_template(
            'index.html',
            logged_in=False,
            show_2fa=False,
            qr_code=None,
            secret=None,
            message='пароли не совпадают'
        )

    if get_user(login):
        return render_template(
            'index.html',
            logged_in=False,
            show_2fa=False,
            qr_code=None,
            secret=None,
            message='логин занят'
        )

    secret = pyotp.random_base32()
    password_hash = generate_password_hash(password)
    created_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    create_user(login, password_hash, secret, created_at)

    qr_code = make_qr_base64(secret, login)
    text = 'Аккаунт создан. Сохраните секрет и QR сейчас, потом они больше не покажутся.'
    return render_template(
        'index.html',
        logged_in=False,
        show_2fa=False,
        qr_code=qr_code,
        secret=secret,
        message=text
    )


@app.route('/login', methods=['POST'])
def login():
    login = request.form.get('login', '').strip()
    password = request.form.get('password', '')
    user = get_user(login)

    if not user:
        return render_template(
            'index.html',
            logged_in=False,
            show_2fa=False,
            qr_code=None,
            secret=None,
            message='неверный логин или пароль'
        )

    if not check_password_hash(user[2], password):
        return render_template(
            'index.html',
            logged_in=False,
            show_2fa=False,
            qr_code=None,
            secret=None,
            message='неверный логин или пароль'
        )

    session['pre_2fa_login'] = login
    return render_template(
        'index.html',
        logged_in=False,
        show_2fa=True,
        qr_code=None,
        secret=None,
        message=''
    )


@app.route('/check_2fa', methods=['POST'])
def check_2fa():
    code = request.form.get('code', '').strip()
    login = session.get('pre_2fa_login')

    if not login:
        return render_template(
            'index.html',
            logged_in=False,
            show_2fa=False,
            qr_code=None,
            secret=None,
            message='сначала войдите'
        )

    user = get_user(login)
    if not user:
        return render_template(
            'index.html',
            logged_in=False,
            show_2fa=False,
            qr_code=None,
            secret=None,
            message='пользователь не найден'
        )

    totp = pyotp.TOTP(user[3])
    if not totp.verify(code):
        return render_template(
            'index.html',
            logged_in=False,
            show_2fa=True,
            qr_code=None,
            secret=None,
            message='неверный код'
        )

    session.permanent = True
    session.pop('pre_2fa_login', None)
    session['logged_in'] = True
    session['login'] = login
    session.modified = True
    session['user_id'] = user[0]

    return redirect('/')


@app.route('/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'ok': True})


@app.route('/api/check_players', methods=['POST'])
def api_check_players():
    if not session.get('logged_in'):
        return jsonify({'error': 'no auth'}), 401

    data = request.get_json(silent=True) or {}

    nickname1 = data.get('nickname1', '').strip()
    region1 = data.get('region1', 'RU').strip().upper()
    nickname2 = data.get('nickname2', '').strip()
    region2 = data.get('region2', 'RU').strip().upper()

    if nickname1:
        add_player_to_queue_if_not_exists(nickname1, region1)

    if nickname2:
        add_player_to_queue_if_not_exists(nickname2, region2)

    return jsonify({'ok': True})




"""@app.route('/api/player', methods=['GET'])
def api_player():
    if not session.get('logged_in'):
        return jsonify({'error': 'no auth'}), 401

    nickname = request.args.get('nickname', '').strip()
    region = request.args.get('region', 'RU').strip().upper()

    if not nickname:
        return jsonify(empty_player_block())

    row = get_player(nickname, region)
    if row is None:
        return jsonify(empty_player_block())

    kd_value = row['kd'] if row['kd'] is not None else 0

    return jsonify({
        'nickname': row['nickname'],
        'region': row['region'],
        'kills': row['kills'] if row['kills'] is not None else 0,
        'deaths': row['deaths'] if row['deaths'] is not None else 0,
        'hours_played': row['hours_played'] if row['hours_played'] is not None else 0,
        'kd': round(kd_value, 2),
        'last_online': row['last_online'] or 'не найден',
        'updated_at': row['updated_at'] or '-'
    })"""