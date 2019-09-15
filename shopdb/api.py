#!/usr/bin/env python3

from shopdb.models import *
import shopdb.exceptions as exc
from shopdb.helpers.stocktakings import _get_balance_between_stocktakings
from flask import (Flask, request, g, make_response, jsonify,
                   send_from_directory, render_template)
from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
from werkzeug.exceptions import NotFound, MethodNotAllowed
import jwt
import base64
import sqlite3
import sqlalchemy
import glob
from sqlalchemy.sql import exists
from sqlalchemy.exc import *
from functools import wraps, reduce
import datetime
import configuration as config
import random
import os
import collections
from PIL import Image
import shutil
import pdfkit

app = Flask(__name__)

# Default app settings (to suppress unittest warnings) will be overwritten.
app.config.from_object(config.BaseConfig)

# Setting strict slash mapping to false ('/foo/' and '/foo' are same this way)
app.url_map.strict_slashes = False

db.init_app(app)
bcrypt = Bcrypt(app)


def set_app(configuration):
    """
    Sets all parameters of the applications to those defined in the dictionary
    "configuration" and returns the application object.

    :param configuration: The dictionary with all settings for the application

    :return:              The application object with the updated settings.
    """
    app.config.from_object(configuration)
    return app


def convert_minimal(data, fields):
    """
    This function returns only the required attributes of all objects in
    given list.

    :param data:   The object from which the attributes are obtained.
    :param fields: A list of all attributes to be output.

    :return:       A dictionary with all requested attributes.
    """

    if not isinstance(data, list):
        data = [data]

    if len(data) == 0:
        return []
    out = []

    for item in data:
        element = {}
        for field in fields:
            element[field] = getattr(item, field, None)

        out.append(element)

    return out


def check_forbidden(data, allowed_fields, row):
    """
    This function checks whether any illegal fields exist in the data sent to
    the API with the request. If so, an exception is raised and the request
    is canceled.

    :param data:             The data sent to the API.
    :param allowed_fields:   A list of all allowed fields.
    :param row:              The object for which you want to check whether the
                             fields are forbidden.

    :return:                 None

    :raises ForbiddenField : If a forbidden field is in the data.
    """
    for item in data:
        if (item not in allowed_fields) and (hasattr(row, item)):
            raise exc.ForbiddenField()


def check_fields_and_types(data, required, optional=None):
    """
    This function checks the given data for its types and existence.
    Required fields must exist, optional fields must not.

    :param data:            The data sent to the API.
    :param required:        A dictionary with all required entries and their
                            types.
    :param optional:        A dictionary with all optional entries and their
                            types.

    :return:                None

    :raises DataIsMissing:  If a required field is not in the data.
    :raises WrongType:      If a field is of the wrong type.
    """

    if required and optional:
        allowed = dict(**required, **optional)
    elif required:
        allowed = required
    else:
        allowed = optional

    # Check if there is an unknown field in the data
    if not all(x in allowed for x in data):
        raise exc.UnknownField()

    # Check whether all required data is available
    if required and any(item not in data for item in required):
        raise exc.DataIsMissing()

    # Check all data (including optional data) for their types
    for key, value in data.items():
        if not isinstance(value, allowed.get(key)):
            raise exc.WrongType()


def check_allowed_parameters(allowed):
    """
    This method checks all GET parameters for their type.

    :param allowed:               A dictionary containing all allowed parameters
                                  and types.

    :return:                      A dictionary with all converted and checked
                                  parameters.

    :raises UnauthorizedAccess:   If there's an illegal parameter in the data.
    :raises WrongType:            If an argument is of the wrong type.
    """
    result = {}
    if any([argument not in allowed for argument in request.args]):
        raise exc.UnauthorizedAccess()

    for key in request.args:
        try:
            result[key] = allowed[key](request.args.get(key))
        except ValueError:
            raise exc.WrongType()

    return result


def update_fields(data, row, updated=None):
    """
    This helper function updates all fields defined in the dictionary "data"
    for a given database object "row". If modifications have already been made
    to the object, the names of the fields that have already been updated can
    be transferred with the "updated" list. All updated fields are added to
    this list.

    :param data:               The dictionary with all entries to be updated.
    :param row:                The database object to be updated.
    :param updated:            A list of all fields that have already been
                               updated.

    :return:                   A list with all already updated fields and
                               those that have been added.

    :raises NothingHasChanged: If no fields were changed during the update.
    """
    for item in data:
        if not getattr(row, item) == data[item]:
            setattr(row, item, data[item])
            if updated is not None:
                updated.append(item)
            else:
                updated = [item]

    if not updated or len(updated) == 0:
        raise exc.NothingHasChanged()

    return updated


def insert_user(data):
    """
    This help function creates a new user with the given data.

    :param data:                 Is the dictionary containing the data for the
                                 new user.

    :return:                     None

    :raises DataIsMissing:       If not all required data is available.
    :raises WrongType:           If one or more data is of the wrong type.
    :raises PasswordsDoNotMatch: If the passwords do not match.
    :raises CouldNotCreateEntry: If the new user cannot be created.
    """

    required = {'lastname': str}
    optional = {'firstname': str, 'password': str, 'password_repeat': str}

    check_fields_and_types(data, required, optional)

    password = None

    if 'password' in data:
        if 'password_repeat' not in data:
            raise exc.DataIsMissing()

        password = data['password'].strip()
        repeat_password = data['password_repeat'].strip()

        # Check if the passwords match.
        if password != repeat_password:
            raise exc.PasswordsDoNotMatch()

        # Check the password length
        if len(password) < app.config['MINIMUM_PASSWORD_LENGTH']:
            raise exc.PasswordTooShort()

        password = bcrypt.generate_password_hash(data['password'])

    # Try to create the user.
    if 'firstname' in data:
        firstname = data['firstname']
    else:
        firstname = None
    try:
        user = User(
            firstname=firstname,
            lastname=data['lastname'],
            password=password)
        db.session.add(user)
    except IntegrityError:
        raise exc.CouldNotCreateEntry()


def insert_deposit(data, admin):
    """
    This help function creates a new deposit with the given data.

    :raises DataIsMissing:       If not all required data is available.
    :raises WrongType:           If one or more data is of the wrong type.
    :raises EntryNotFound:       If the user with this ID does not exist.
    :raises UserIsNotVerified:   If the user has not yet been verified.
    :raises InvalidAmount:       If amount is equal to zero.
    :raises CouldNotCreateEntry: If any other error occurs.
    """

    required = {'user_id': int, 'amount': int, 'comment': str}
    check_fields_and_types(data, required)

    # Check user
    user = User.query.filter_by(id=data['user_id']).first()
    if not user:
        raise exc.EntryNotFound()

    # Check if the user has been verified.
    if not user.is_verified:
        raise exc.UserIsNotVerified()

    # Check if the user is inactive
    if not user.active:
        raise exc.UserIsInactive()

    # Check amount
    if data['amount'] == 0:
        raise exc.InvalidAmount()

    # Create and insert deposit
    try:
        deposit = Deposit(**data)
        deposit.admin_id = admin.id
        db.session.add(deposit)
    except IntegrityError:
        raise exc.CouldNotCreateEntry()


def checkIfUserIsValid(f):
    """
    This function checks whether the requested user exists, has been verified and is active.
    If this is not the case the request will be blocked.

    :param f:                  Is the wrapped function.

    :return:                   The wrapped function f with the additional parameter user.

    :raises EntryNotFound:     If the user with this ID does not exist.
    :raises UserIsNotVerified: If the user with this ID has not been verified yet.
    :raises UserIsInactive:    If the user with this ID is inactive.
    """

    @wraps(f)
    def decorator(*args, **kwargs):
        user = User.query.filter_by(id=kwargs['id']).first()
        if not user:
            raise exc.EntryNotFound()

        # Check if the user has been verified.
        if not user.is_verified:
            raise exc.UserIsNotVerified()

        # Check if the user is inactive
        if not user.active:
            raise exc.UserIsInactive()

        # If all criteria are met, the requested function can be executed.
        return f(user, *args, **kwargs)

    return decorator


def adminRequired(f):
    """
    This function checks whether a valid token is contained in the request.
    If this is not the case, or the user has no admin rights, the request
    will be blocked.

    :param f:                   Is the wrapped function.

    :return:                    The wrapped function f with the additional
                                parameter admin.

    :raises UnauthorizedAccess: If no token object can be found in the request
                                header.
    :raises TokenIsInvalid:     If the token cannot be decoded.
    :raises TokenHasExpired:    If the token has been expired.
    :raises TokenIsInvalid:     If no user object could be found in the
                                decoded token.
    :raises UnauthorizedAccess: The user has no administrator privileges.
    """

    @wraps(f)
    def decorated(*args, **kwargs):
        # Does the request header contain a token?
        try:
            token = request.headers['token']
        except KeyError:
            raise exc.UnauthorizedAccess()

        # Is the token valid?
        try:
            data = jwt.decode(token, app.config['SECRET_KEY'])
        except jwt.exceptions.DecodeError:
            raise exc.TokenIsInvalid()
        except jwt.ExpiredSignatureError:
            raise exc.TokenHasExpired()

        # If there is no admin object in the token and does the user does have
        # admin rights?
        try:
            admin_id = data['user']['id']
            admin = User.query.filter(User.id == admin_id).first()
            assert admin.is_admin is True
        except KeyError:
            raise exc.TokenIsInvalid()
        except AssertionError:
            raise exc.UnauthorizedAccess()

        # At this point it was verified that the request comes from an
        # admin and the request is executed. In addition, the user is
        # forwarded to the following function so that the administrator
        # responsible for any changes in the database can be traced.
        return f(admin, *args, **kwargs)

    return decorated


def adminOptional(f):
    """
    This function checks whether a valid token is contained in the request.
    If this is not the case, or the user has no admin rights, the following
    function returns only a part of the available data.

    :param f:                Is the wrapped function.

    :return:                 Returns the wrapped function f with the
                             additional parameter admin, if present.
                             Otherwise, the parameter admin is None.

    :raises TokenIsInvalid:  If the token cannot be decoded.
    :raises TokenHasExpired: If the token has been expired.
    :raises TokenIsInvalid:  If no user object could be found in the decoded
                             token.
    """

    @wraps(f)
    def decorated(*args, **kwargs):
        # Does the request header contain a token?
        try:
            token = request.headers['token']
        except KeyError:
            return f(None, *args, **kwargs)

        # Is the token valid?
        try:
            data = jwt.decode(token, app.config['SECRET_KEY'])
        except jwt.exceptions.DecodeError:
            raise exc.TokenIsInvalid()
        except jwt.ExpiredSignatureError:
            raise exc.TokenHasExpired()

        # If there is no admin object in the token and does the user does have
        # admin rights?
        try:
            admin_id = data['user']['id']
            admin = User.query.filter(User.id == admin_id).first()
            assert admin.is_admin is True
        except KeyError:
            raise exc.TokenIsInvalid()
        except AssertionError:
            return f(None, *args, **kwargs)

        # At this point it was verified that the request comes from an
        # admin and the request is executed. In addition, the user is
        # forwarded to the following function so that the administrator
        # responsible for any changes in the database can be traced.
        return f(admin, *args, **kwargs)

    return decorated


@app.before_request
def before_request_hook():
    """
    This function is executed before each request is processed. Its purpose is
    to check whether the application is currently in maintenance mode. If this
    is the case, the current request is aborted and a corresponding exception
    is raised. An exception to this is a request on the route
    "/maintenance" [POST]: Via this route the maintenance mode can be switched
    on and off (by an administrator) or on the route "/login", so that one can
    log in as administrator.

    :raises MaintenanceMode: if the application is in maintenance mode.
    """

    # Check for maintenance mode.
    exceptions = ['maintenance', 'login']

    if app.config.get('MAINTENANCE') and request.endpoint not in exceptions:
        raise exc.MaintenanceMode()


@app.errorhandler(Exception)
def handle_error(error):
    """
    This wrapper catches all exceptions and, if possible, returns a user
    friendly response. Otherwise, it will raise the error

    :param error: Is the exception to be raised.

    :return:      Return a json response with the message that the page cannot
                  be found if it is a 404 error.
    :return:      Return a json response with the custom exception message,
                  if it is a custom exception.

    :raises:      Raises the passed exception if the application is in debug
                  mode or cannot be interpreted.
    """
    # Perform a rollback. All changes that have not yet been committed are
    # thus reset.
    db.session.rollback()

    # Catch the 404-error.
    if isinstance(error, NotFound):
        return jsonify(result='error', message='Page does not exist.'), 404

    # Catch the 'MethodNotAllowed' exception
    if isinstance(error, MethodNotAllowed):
        return jsonify(result='error', message='Method not allowed.'), 405

    # As long as the application is in debug mode, all other exceptions
    # should be output immediately.
    if app.config['DEBUG'] and not app.config['DEVELOPMENT']:
        raise error  # pragma: no cover

    # Create, if possible, a user friendly response.
    if isinstance(error, exc.ShopdbException):
        return jsonify(result=error.type, message=error.message), error.code
    else:  # pragma: no cover
        raise error


def json_body():
    """
    Returns the json data from the current request.

    :return:             The json body from the current request.

    :raises InvalidJSON: If the json data cannot be interpreted.
    """
    jb = request.get_json()
    if jb is None:
        raise exc.InvalidJSON()
    return jb


@app.route('/', methods=['GET'])
def index():
    """
    A route that simply returns that the backend is online.

    :return: A message which says that the backend is online.
    """
    return jsonify({'message': 'Backend is online.'})


@app.route('/maintenance', methods=['POST'], endpoint='maintenance')
@adminRequired
def set_maintenance(admin):
    """
    This route can be used by an administrator to switch the maintenance mode
    on or off.

    :param admin:              Is the administrator user, determined by
                               @adminRequired.

    :raises DataIsMissing:     If the maintenance state is not included
                               in the request.
    :raises UnknownField:      If an unknown parameter exists in the request
                               data.
    :raises InvalidType:       If one or more parameters have an invalid type.
    :raises NothingHasChanged: If the maintenance mode is not changed by the
                               request.

    :return:                   A message with the new maintenance mode.
    """

    data = json_body()
    # Check all items in the json body.
    required = {'state': bool}
    check_fields_and_types(data, required)

    # Get the current maintenance state.
    current_state = app.config['MAINTENANCE']

    # Get the new state.
    new_state = data['state']

    # Handle the request.
    if current_state == new_state:
        raise exc.NothingHasChanged()

    app.config['MAINTENANCE'] = new_state

    message = 'Turned maintenance mode ' + ('on.' if new_state else 'off.')
    return jsonify({'message': message})


@app.route('/images', methods=['GET'], defaults={'imagename': None})
@app.route('/images/<imagename>', methods=['GET'])
def get_image(imagename):
    """
    A picture can be requested via this route. If the image is not found or if
    the image name is empty, a default image will be returned.

    :param imagename: Is the name of the requested image.

    :return:          The requested image or the default image, if applicable.
    """
    if not imagename:
        return send_from_directory(app.config['UPLOAD_FOLDER'], 'default.png')
    else:
        if os.path.isfile(app.config['UPLOAD_FOLDER'] + imagename):
            return send_from_directory(app.config['UPLOAD_FOLDER'], imagename)
        else:
            raise exc.EntryNotFound()


@app.route('/upload', methods=['POST'])
@adminRequired
def upload(admin):
    """
    You can upload pictures via this route. The valid file formats can be
    set in the configuration under "VALID_EXTENSIONS".

    :param admin:                 Is the administrator user, determined by
                                  @adminRequired.

    :return:                      The generated file name under which the image
                                  has been stored.

    :raises NoFileIncluded:       If no data was found in the request.
    :raises InvalidFilename:      If the filename is empty empty or invalid in
                                  any other form.
    :raises InvalidFileType:      If the file format is not allowed.
    :raises FileTooLarge:         If the size of the file exceeds the maximum
                                  allowed file size.
    :raises BrokenImage:          If the image file could not be read.
    :raises ImageMustBeQuadratic: If the height and width of the image are not
                                  identical.
    :raises CouldNotCreateEntry:  If the new image cannot be added to the
                                  database.
    """
    # Get the file. Raise an exception if there is no data.
    file = request.get_json()
    if not file:
        raise exc.NoFileIncluded()

    # Check if the filename is empty. There is no way to create a file with
    # empty filename in python so this can not be tested. Anyway, this is
    # a possible error vector.
    if 'filename' not in file or file['filename'] == '':
        raise exc.InvalidFilename()

    # Check if the filename is valid
    filename = file['filename'].split('.')[0]
    if filename is '' or not filename:
        raise exc.InvalidFilename()

    # Check the file extension
    extension = file['filename'].split('.')[1].lower()
    valid_extension = extension in app.config['VALID_EXTENSIONS']
    if not valid_extension:
        raise exc.InvalidFileType()

    # Check if the image data has a value field.
    if 'value' not in file:
        raise exc.NoFileIncluded()

    # Check the content size.
    if len(file['value']) > app.config.get('MAX_CONTENT_LENGTH'):
        raise exc.FileTooLarge()

    # Check if the image is a valid image file.
    try:
        # Save the image to a temporary file.
        temp_filename = '/tmp/' + file['filename']
        filedata = file['value']
        f = open(temp_filename, 'wb')
        f.write(base64.b64decode(filedata))
        f.close()
        image = Image.open(temp_filename)

    # An invalid file will lead to an exception.
    except IOError:
        os.remove(temp_filename)
        raise exc.BrokenImage()

    # Check the real extension again
    if image.format not in [x.upper() for x in app.config['VALID_EXTENSIONS']]:
        raise exc.InvalidFileType()

    # Check aspect ratio
    width, height = image.size
    if width != height:
        raise exc.ImageMustBeQuadratic()

    # Create a unique filename.
    can_be_used = False
    while not can_be_used:
        unique = ''.join(random.choice('0123456789abcdef') for i in range(32))
        filename = '.'.join([unique, extension])
        path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        can_be_used = not os.path.isfile(path)

    # Move the temporary image to its destination path.
    shutil.move(temp_filename, path)

    # Create an upload
    try:
        upload = Upload(filename=filename, admin_id=admin.id)
        db.session.add(upload)
        db.session.commit()
    except IntegrityError:
        raise exc.CouldNotCreateEntry()

    # Make response
    return jsonify({
        'message': 'Image uploaded successfully.',
        'filename': filename}), 200


# Backups route ###################################################
@app.route('/backups', methods=['GET'])
@adminRequired
def list_backups(admin):
    """
    Returns a dictionary with all backups in the backup folder.
    The following backup directory structure is assumed for this function:

    [Year]/[Month]/[Day]/shop-db_[Year]-[Month]-[Day]_[Hour]-[Minute].dump

    For example:
    2019/02/07/shop-db_2019-02-07_15-00.dump

    :return: A dictionary containing all backups and the timestamp of the
             latest backup.
    """
    data = {
        'backups': {},
        'latest': None
    }
    root_dir = app.config['BACKUP_DIR']
    start = root_dir.rfind(os.sep) + 1
    for path, dirs, files in os.walk(root_dir):
        # Ignore the root path
        if os.path.normpath(path) == os.path.normpath(root_dir):
            continue
        # Ignore all empty folders
        if not dirs and not files:
            continue

        folders = path[start:].split(os.sep)
        subdir = dict.fromkeys(files)

        parent = reduce(dict.get, folders[:-1], data['backups'])

        # We are in the day-directory of our tree
        if len(subdir) != 0:
            parent[folders[-1]] = [key for key in subdir.keys()]
        else:
            parent[folders[-1]] = subdir

    # Get the timestamp of the latest backup
    all_files = glob.glob(root_dir + '**/*.dump', recursive=True)
    if all_files:
        latest = os.path.getctime(max(all_files, key=os.path.getctime))
        data['latest'] = datetime.datetime.fromtimestamp(latest)

    return jsonify(data)


# Financial overview route ###################################################
@app.route('/financial_overview', methods=['GET'])
@adminRequired
def get_financial_overview(admin):
    """
    The financial status of the entire project can be retrieved via this route.
    All purchases, deposits, payoffs, refunds and replenishmentcollections are
    used for this purpose. The items are cleared once to a number indicating
    whether the community has debt or surplus money. In addition, the
    individual items are returned separately in order to get a better
    breakdown of the items.

    :param admin: Is the administrator user, determined by @adminRequired.

    :return:      A dictionary with the individually calculated values.
    """

    # Query all purchases,
    purchases = Purchase.query.filter(Purchase.revoked.is_(False)).all()

    # Query all deposits.
    deposits = Deposit.query.filter(Deposit.revoked.is_(False)).all()

    # Query all turnovers.
    turnovers = Turnover.query.filter(Turnover.revoked.is_(False)).all()

    # Query all payoffs.
    payoffs = Payoff.query.filter(Payoff.revoked.is_(False)).all()

    # Query all refunds.
    refunds = Refund.query.filter(Refund.revoked.is_(False)).all()

    # Query all replenishment collections.
    replcolls = (ReplenishmentCollection
                 .query
                 .filter(ReplenishmentCollection.revoked.is_(False))
                 .all())

    # Get the balance between the first and the last stocktaking.
    # If there is no stocktaking or only one stocktaking, the balance is 0.
    stock_first = (StocktakingCollection.query
                   .order_by(StocktakingCollection.id)
                   .first())
    stock_last = (StocktakingCollection.query
                  .order_by(StocktakingCollection.id.desc())
                  .first())

    if not all([stock_first, stock_last]) or stock_first is stock_last:
        pos_stock = 0
        neg_stock = 0
    else:
        balance = _get_balance_between_stocktakings(stock_first, stock_last)
        pos_stock = balance['profit']
        neg_stock = balance['loss']

    # Incomes are:
    # - Purchases                    with a positive price
    # - Deposits                     with a positive amount
    # - Turnovers                    with a positive amount
    # - Replenishmentcollections     with a negative price
    # - Refunds                      with a negative amount
    # - Payoffs                      with a negative amount
    # - Profits between stocktakings

    pos_pur = sum(
        map(abs, list(filter(lambda x: x >= 0,
                             list(map(lambda x: x.price, purchases)))))
    )

    pos_dep = sum(
        map(abs, list(filter(lambda x: x >= 0,
                             list(map(lambda x: x.amount, deposits)))))
    )

    pos_turn = sum(
        map(abs, list(filter(lambda x: x >= 0,
                             list(map(lambda x: x.amount, turnovers)))))
    )

    neg_rep = sum(
        map(abs, list(filter(lambda x: x < 0,
                             list(map(lambda x: x.price, replcolls)))))
    )

    neg_ref = sum(
        map(abs, list(filter(lambda x: x < 0,
                             list(map(lambda x: x.total_price, refunds)))))
    )

    neg_pay = sum(
        map(abs, list(filter(lambda x: x < 0,
                             list(map(lambda x: x.amount, payoffs)))))
    )

    sum_incomes = sum([
        pos_pur, pos_dep, pos_turn, neg_rep, neg_ref, neg_pay, pos_stock
    ])

    incomes = {
        'amount': sum_incomes,
        'items': [
            {'name': 'Purchases', 'amount': pos_pur},
            {'name': 'Deposits', 'amount': pos_dep},
            {'name': 'Turnovers', 'amount': pos_turn},
            {'name': 'Replenishments', 'amount': neg_rep},
            {'name': 'Refunds', 'amount': neg_ref},
            {'name': 'Payoffs', 'amount': neg_pay},
            {'name': 'Stocktakings', 'amount': pos_stock}
        ]
    }

    # Expenses are:
    # - Purchases                with a negative price
    # - Deposits                 with a negative amount
    # - Turnovers                with a negative amount
    # - Replenishmentcollections with a positive price
    # - Refunds                  with a positive amount
    # - Payoffs                  with a positive amount
    # - Losses between stocktakings
    neg_pur = sum(
        map(abs, list(filter(lambda x: x < 0,
                             list(map(lambda x: x.price, purchases)))))
    )

    neg_dep = sum(
        map(abs, list(filter(lambda x: x < 0,
                             list(map(lambda x: x.amount, deposits)))))
    )

    neg_turn = sum(
        map(abs, list(filter(lambda x: x < 0,
                             list(map(lambda x: x.amount, turnovers)))))
    )

    pos_rep = sum(
        map(abs, list(filter(lambda x: x >= 0,
                             list(map(lambda x: x.price, replcolls)))))
    )

    pos_ref = sum(
        map(abs, list(filter(lambda x: x >= 0,
                             list(map(lambda x: x.total_price, refunds)))))
    )

    pos_pay = sum(
        map(abs, list(filter(lambda x: x >= 0,
                             list(map(lambda x: x.amount, payoffs)))))
    )

    sum_expenses = sum([
        neg_pur, neg_dep, neg_turn, pos_rep, pos_ref, pos_pay, neg_stock
    ])

    expenses = {
        'amount': sum_expenses,
        'items': [
            {'name': 'Purchases', 'amount': neg_pur},
            {'name': 'Deposits', 'amount': neg_dep},
            {'name': 'Turnovers', 'amount': neg_turn},
            {'name': 'Replenishments', 'amount': pos_rep},
            {'name': 'Refunds', 'amount': pos_ref},
            {'name': 'Payoffs', 'amount': pos_pay},
            {'name': 'Stocktakings', 'amount': neg_stock}
        ]
    }

    # The total balance is calculated as incomes minus expenses.
    total_balance = sum_incomes - sum_expenses

    financial_overview = {
        'total_balance': total_balance,
        'incomes': incomes,
        'expenses': expenses
    }
    return jsonify({'financial_overview': financial_overview}), 200


# Login route ################################################################
@app.route('/login', methods=['POST'], endpoint='login')
def login():
    """
    Registered users can log in on this route.

    :return:                    A temporary valid token, which users can use
                                to identify themselves when making requests to
                                the API.

    :raises DataIsMissing:      If the id or password (or both) is not included
                                in the request.
    :raises UnknownField:       If an unknown parameter exists in the request
                                data.
    :raises InvalidType:        If one or more parameters have an invalid type.
    :raises InvalidCredentials: If no user can be found with the given data.
    :raises UserIsNotVerified:  If the user has not yet been verified.
    """
    data = json_body()
    # Check all items in the json body.
    required = {'id': int, 'password': str}
    check_fields_and_types(data, required)

    # Try to get the user with the id
    user = User.query.filter_by(id=data['id']).first()

    # If no user with this data exists cancel the authentication.
    if not user:
        raise exc.InvalidCredentials()

    # Check if the user has already been verified.
    if not user.is_verified:
        raise exc.UserIsNotVerified()

    # Check if the user is inactive
    if not user.active:
        raise exc.UserIsInactive()

    # Check if the user has set a password.
    if not user.password:
        raise exc.InvalidCredentials()

    # Check if the password matches the user's password.
    if not bcrypt.check_password_hash(user.password, str(data['password'])):
        raise exc.InvalidCredentials()

    # Create a dictionary object of the user.
    fields = ['id', 'firstname', 'lastname', 'credit']
    d_user = convert_minimal(user, fields)[0]

    # Create a token.
    exp = datetime.datetime.now() + datetime.timedelta(minutes=60)
    token = jwt.encode({'user': d_user, 'exp': exp}, app.config['SECRET_KEY'])

    # Return the result.
    return jsonify({'result': True, 'token': token.decode('UTF-8')}), 200


# Register route #############################################################
@app.route('/register', methods=['POST'])
def register():
    """
    Registration of new users.

    :return:                     A message that the registration was successful.

    :raises CouldNotCreateEntry: If the new user cannot be created.
    """
    insert_user(json_body())
    try:
        db.session.commit()
    except IntegrityError:
        raise exc.CouldNotCreateEntry()

    return jsonify({'message': 'Created user.'}), 200


# Verification routes #########################################################
@app.route('/verifications', methods=['GET'])
@adminRequired
def list_pending_validations(admin):
    """
    Returns a list of all non verified users.

    :param admin: Is the administrator user, determined by @adminRequired.

    :return:      A list of all non verified users.
    """
    res = (db.session.query(User)
           .filter(~exists().where(UserVerification.user_id == User.id))
           .all())
    fields = ['id', 'firstname', 'lastname']
    return jsonify({'pending_validations': convert_minimal(res, fields)}), 200


@app.route('/verify/<int:id>', methods=['POST'])
@adminRequired
def verify_user(admin, id):
    """
    Verify a user.

    :param admin:                Is the administrator user, determined by
                                 @adminRequired.
    :param id:                   Is the user id.

    :return:                     A message that the verification was successful.

    :raises UserAlreadyVerified: If the user already has been verified.
    :raises DataIsMissing:       If the rank_id is not included in the request.
    :raises UnknownField:        If an unknown parameter exists in the request
                                 data.
    :raises InvalidType:         If one or more parameters have an invalid
                                 type.
    :raises EntryNotFound:       If the rank to be assigned to the user does
                                 not exist.
    """
    user = User.query.filter_by(id=id).first()
    if not user:
        raise exc.EntryNotFound()
    if user.is_verified:
        raise exc.UserAlreadyVerified()

    data = json_body()
    # Check all items in the json body.
    required = {'rank_id': int}
    check_fields_and_types(data, required)

    rank_id = data['rank_id']
    rank = Rank.query.filter_by(id=rank_id).first()
    if not rank:
        raise exc.EntryNotFound()

    user.verify(admin_id=admin.id, rank_id=rank_id)
    db.session.commit()
    return jsonify({'message': 'Verified user.'}), 201


# User routes ################################################################
from shopdb.routes.users import *  # noqa: E402


# Rank routes #############################################################
from shopdb.routes.ranks import *  # noqa: E402


# Tag routes #############################################################
from shopdb.routes.tags import *  # noqa: E402


# Tag assignment routes ######################################################
from shopdb.routes.tagassignments import *  # noqa: E402


# Product routes #############################################################
from shopdb.routes.products import *  # noqa: E402


# Purchase routes ############################################################
from shopdb.routes.purchases import *  # noqa: E402


# Deposit routes #############################################################
from shopdb.routes.deposits import *  # noqa: E402


# ReplenishmentCollection routes ##############################################
from shopdb.routes.replenishmentcollections import *  # noqa: E402


# Refund routes ##############################################################
from shopdb.routes.refunds import *  # noqa: E402


# Payoff routes ##############################################################
from shopdb.routes.payoffs import *  # noqa: E402


# StocktakingCollection routes ##############################################
from shopdb.routes.stocktakingcollections import *  # noqa: E402


# Turnover routes #############################################################
@app.route('/turnovers', methods=['GET'])
@adminRequired
def list_turnovers(admin):
    """
    Returns a list of all turnovers.

    :param admin: Is the administrator user, determined by @adminRequired.

    :return:      A list of all turnovers.
    """
    turnovers = Turnover.query.all()
    fields = ['id', 'timestamp', 'amount', 'comment', 'revoked', 'admin_id']
    return jsonify({'turnovers': convert_minimal(turnovers, fields)}), 200


@app.route('/turnovers', methods=['POST'])
@adminRequired
def create_turnover(admin):
    """
    Insert a new turnover.

    :param admin:                Is the administrator user, determined by
                                 @adminRequired.

    :return:                     A message that the creation was successful.

    :raises DataIsMissing:       If not all required data is available.
    :raises WrongType:           If one or more data is of the wrong type.
    :raises InvalidAmount:       If amount is equal to zero.
    :raises CouldNotCreateEntry: If any other error occurs.
    """
    data = json_body()
    required = {'amount': int, 'comment': str}
    check_fields_and_types(data, required)

    # Check amount
    if data['amount'] == 0:
        raise exc.InvalidAmount()

    # Create and insert turnover
    try:
        turnover = Turnover(**data)
        turnover.admin_id = admin.id
        db.session.add(turnover)
        db.session.commit()
    except IntegrityError:
        raise exc.CouldNotCreateEntry()

    return jsonify({'message': 'Created turnover.'}), 200


@app.route('/turnovers/<int:id>', methods=['GET'])
def get_turnover(id):
    """
    Returns the turnover with the requested id.

    :param id:             Is the turnover id.

    :return:               The requested turnover as JSON object.

    :raises EntryNotFound: If the turnover with this ID does not exist.
    """
    # Query the turnover
    res = Turnover.query.filter_by(id=id).first()
    # If it not exists, return an error
    if not res:
        raise exc.EntryNotFound()
    # Convert the turnover to a JSON friendly format
    fields = ['id', 'timestamp', 'amount', 'comment', 'revoked',
              'revokehistory']
    return jsonify({'turnover': convert_minimal(res, fields)[0]}), 200


@app.route('/turnovers/<int:id>', methods=['PUT'])
@adminRequired
def update_turnover(admin, id):
    """
    Update the turnover with the given id.

    :param admin:                Is the administrator user, determined by
                                 @adminRequired.
    :param id:                   Is the turnover id.

    :return:                     A message that the update was
                                 successful and a list of all updated fields.

    :raises EntryNotFound:       If the turnover with this ID does not exist.
    :raises ForbiddenField:      If a forbidden field is in the request data.
    :raises UnknownField:        If an unknown parameter exists in the request
                                 data.
    :raises InvalidType:         If one or more parameters have an invalid type.
    :raises NothingHasChanged:   If no change occurred after the update.
    :raises CouldNotUpdateEntry: If any other error occurs.
    """
    # Check turnover
    turnover = Turnover.query.filter_by(id=id).first()
    if not turnover:
        raise exc.EntryNotFound()

    data = json_body()

    if not data:
        raise exc.NothingHasChanged()

    updateable = {'revoked': bool}
    check_forbidden(data, updateable, turnover)
    check_fields_and_types(data, None, updateable)

    # Handle turnover revoke
    if 'revoked' in data:
        if turnover.revoked == data['revoked']:
            raise exc.NothingHasChanged()
        turnover.toggle_revoke(revoked=data['revoked'], admin_id=admin.id)

    # Apply changes
    try:
        db.session.commit()
    except IntegrityError:
        raise exc.CouldNotUpdateEntry()

    return jsonify({
        'message': 'Updated turnover.',
    }), 201
