#!/usr/bin/env python3
"""Envía notificaciones Web Push usando pywebpush + llaves VAPID.
Secrets requeridos (env):
  PUSH_SUBSCRIPTION  -> JSON de la suscripción (copiado desde la app)
  VAPID_PRIVATE      -> llave privada VAPID (base64url raw)
"""
import os
import json


def send(notifications):
    """notifications: lista de dicts {'title':..., 'body':..., 'tag':...}"""
    sub_raw = os.environ.get('PUSH_SUBSCRIPTION')
    vapid_priv = os.environ.get('VAPID_PRIVATE')
    if not notifications:
        return
    if not (sub_raw and vapid_priv):
        print('  (push desactivado: faltan secrets PUSH_SUBSCRIPTION / VAPID_PRIVATE)')
        return
    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        print('  (pywebpush no instalado, sin push)')
        return
    try:
        sub = json.loads(sub_raw)
    except Exception as e:
        print(f'  (PUSH_SUBSCRIPTION inválida: {e})')
        return
    for n in notifications:
        try:
            webpush(
                subscription_info=sub,
                data=json.dumps(n),
                vapid_private_key=vapid_priv,
                vapid_claims={'sub': 'mailto:jaywrkr@gmail.com'},
            )
            print(f'  🔔 push enviado: {n.get("title")}')
        except WebPushException as e:
            print(f'  (error push: {e})')
        except Exception as e:
            print(f'  (error push: {e})')
