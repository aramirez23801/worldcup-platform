# WebSocket $connect authorizer.
#
# Browsers cannot set custom headers on a WebSocket handshake, so the SPA passes
# the Cognito ID token as a query-string parameter (wss://...?token=<idToken>).
# This authorizer verifies that token offline against the user pool's JWKS and,
# on success, hands the user's "sub" to the $connect route as authorizer context.
import json
import os
import urllib.request

from jose import jwt
from jose.exceptions import JWTError

_REGION = os.environ['AWS_REGION']
_POOL_ID = os.environ['USER_POOL_ID']
_CLIENT_ID = os.environ['CLIENT_ID']

_ISSUER = 'https://cognito-idp.' + _REGION + '.amazonaws.com/' + _POOL_ID
_JWKS_URL = _ISSUER + '/.well-known/jwks.json'

# Cached across warm invocations; Cognito rotates its signing keys rarely.
_jwks_cache = None


def _fetch_jwks():
    with urllib.request.urlopen(_JWKS_URL, timeout=5) as resp:
        return json.loads(resp.read().decode('utf-8'))


def _key_for(kid, allow_refresh=True):
    global _jwks_cache
    if _jwks_cache is None:
        _jwks_cache = _fetch_jwks()
    for key in _jwks_cache.get('keys', []):
        if key.get('kid') == kid:
            return key
    # kid not in cache: a key rotation may have happened, refresh once and retry.
    if allow_refresh:
        _jwks_cache = _fetch_jwks()
        return _key_for(kid, allow_refresh=False)
    return None


def _verify(token):
    header = jwt.get_unverified_header(token)
    key = _key_for(header.get('kid'))
    if key is None:
        raise JWTError('no matching signing key for token')
    claims = jwt.decode(
        token,
        key,
        algorithms=['RS256'],
        audience=_CLIENT_ID,
        issuer=_ISSUER,
        options={'require_aud': True, 'require_exp': True},
    )
    if claims.get('token_use') != 'id':
        raise JWTError('expected an id token, got ' + str(claims.get('token_use')))
    return claims['sub']


def _allow(sub, method_arn):
    return {
        'principalId': sub,
        'policyDocument': {
            'Version': '2012-10-17',
            'Statement': [{
                'Action': 'execute-api:Invoke',
                'Effect': 'Allow',
                'Resource': method_arn,
            }],
        },
        'context': {'sub': sub},
    }


def handler(event, context):
    token = (event.get('queryStringParameters') or {}).get('token')
    if not token:
        print('auth denied: no token on connect request')
        raise Exception('Unauthorized')
    try:
        sub = _verify(token)
    except (JWTError, KeyError) as e:
        # Bad / expired / forged token.
        print('auth denied: invalid token:', type(e).__name__, str(e))
        raise Exception('Unauthorized')
    except Exception as e:
        # JWKS fetch failure or any other unexpected error: fail closed, not a 500.
        print('auth denied: verification error:', type(e).__name__, str(e))
        raise Exception('Unauthorized')
    return _allow(sub, event['methodArn'])