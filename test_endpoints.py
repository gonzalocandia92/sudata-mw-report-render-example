#!/usr/bin/env python3
"""
Prueba de punta a punta la API privada de Sudata PBI.

    python test_endpoints.py                    # todos los reportes de la empresa
    python test_endpoints.py --report-id 3      # solo un reporte
    python test_endpoints.py --filter 2         # ademas prueba el filtro en los reportes filtrables
    python test_endpoints.py --legacy           # contra un servidor sin el hotfix (no exige settings/actions)

Lee API_BASE, CLIENT_ID y CLIENT_SECRET del archivo .env (junto a este script) o de las
variables de entorno; el entorno tiene prioridad. Ejemplo de .env:

    API_BASE = https://reports.sudata.co/private
    CLIENT_ID = ...
    CLIENT_SECRET = ...

Que verifica:
  1. POST /login            token Bearer valido y casos de error (credenciales malas, cuerpo vacio)
  2. GET  /reports          lista con id, name, filterable; rechaza pedidos sin token o con token falso
  3. GET  /report-config    lo necesario para renderizar (embedUrl, reportId, accessToken, workspaceId),
                            que el accessToken sea un JWT de Power BI vigente, y los bloques nuevos
                            settings.persistentFiltersEnabled y actions.{resetToDefault, refreshVisuals}

Restablecer y Actualizar visuales NO son endpoints: se ejecutan en el navegador con el SDK de Power BI
(report.resetPersistentFilters() y report.refresh()). Este script comprueba que el servidor informe
bien si estan habilitadas; para probarlas de verdad abri el front (python app.py).

Nunca imprime el secret ni los tokens. Codigo de salida: 0 todo bien, 1 hubo fallas, 2 error de configuracion.
"""
import argparse
import base64
import json
import os
import sys
import time
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
PLACEHOLDERS = {'', 'client_id_example', 'client_secret_example'}
TIMEOUT = 30  # /report-config llama a Power BI y puede tardar unos segundos


class Results:
    def __init__(self):
        self.passed = self.failed = self.warned = 0

    def ok(self, label, detail=''):
        self.passed += 1
        print(f'  OK    {label}' + (f'  ({detail})' if detail else ''))

    def fail(self, label, detail=''):
        self.failed += 1
        print(f'  FALLA {label}' + (f'  -> {detail}' if detail else ''))

    def warn(self, label, detail=''):
        self.warned += 1
        print(f'  AVISO {label}' + (f'  -> {detail}' if detail else ''))

    def check(self, condition, label, detail_ok='', detail_fail=''):
        (self.ok(label, detail_ok) if condition else self.fail(label, detail_fail))
        return bool(condition)


# -- configuracion ----------------------------------------------------------

def load_env_file(path):
    values = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding='utf-8-sig').splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, _, value = line.partition('=')
        key = key.strip().removeprefix('export ').strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in '"\'':
            value = value[1:-1]
        values[key] = value
    return values


def resolve_config(args):
    file_values = load_env_file(Path(args.env_file))

    def pick(name, cli=None):
        return (cli or os.environ.get(name) or file_values.get(name) or '').strip()

    base = pick('API_BASE', args.base).rstrip('/')
    if base and not base.endswith('/private'):
        base += '/private'
    return base, pick('CLIENT_ID'), pick('CLIENT_SECRET'), Path(args.env_file).is_file()


def mask(value, keep=6):
    return value[:keep] + '...' if len(value) > keep else '***'


# -- http -------------------------------------------------------------------

class Api:
    def __init__(self, base):
        self.base = base
        self.http = requests.Session()

    def call(self, method, path, token=None, **kwargs):
        headers = kwargs.pop('headers', {})
        if token:
            headers['Authorization'] = f'Bearer {token}'
        try:
            return self.http.request(method, self.base + path, headers=headers, timeout=TIMEOUT, **kwargs)
        except requests.RequestException as exc:
            raise SystemExit(f'\nNo pude conectarme a {self.base}{path}: {type(exc).__name__}: {exc}') from None


def body(response):
    try:
        data = response.json()
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def jwt_payload(token):
    """Payload de un JWT sin verificar la firma (solo para mostrar expiracion y audiencia)."""
    try:
        part = token.split('.')[1]
        part += '=' * (-len(part) % 4)
        return json.loads(base64.urlsafe_b64decode(part))
    except Exception:
        return None


# -- pruebas ----------------------------------------------------------------

def test_login(api, res, client_id, client_secret, negative):
    print('\n[1] POST /login')
    r = api.call('POST', '/login', json={'client_id': client_id, 'client_secret': client_secret})
    data = body(r)
    if r.status_code != 200:
        res.fail('login con las credenciales del .env', f'HTTP {r.status_code}: {data.get("error", r.text[:120])}')
        return None
    token = data.get('access_token')
    expires = data.get('expires_in')
    good = (isinstance(token, str) and token.count('.') == 2 and data.get('token_type') == 'Bearer'
            and isinstance(expires, int) and expires > 0)
    if not res.check(good, 'login con las credenciales del .env',
                     f'token Bearer, vence en {expires // 60 if isinstance(expires, int) else "?"} min',
                     f'respuesta inesperada: claves {sorted(data)}'):
        return None
    if negative:
        r = api.call('POST', '/login', json={'client_id': client_id, 'client_secret': 'secret-incorrecto-de-prueba'})
        res.check(r.status_code == 401, 'secret incorrecto -> 401', detail_fail=f'HTTP {r.status_code}')
        r = api.call('POST', '/login', json={})
        res.check(r.status_code == 400, 'cuerpo vacio -> 400', detail_fail=f'HTTP {r.status_code}')
    return token


def test_reports(api, res, token, negative):
    print('\n[2] GET /reports')
    r = api.call('GET', '/reports', token)
    data = body(r)
    if not res.check(r.status_code == 200, 'lista de reportes con token valido',
                     detail_fail=f'HTTP {r.status_code}: {data.get("error", "")}'):
        return []
    reports = data.get('reports')
    shape = (isinstance(reports, list) and 'empresa_id' in data and 'empresa_nombre' in data
             and all(isinstance(x.get('id'), int) and isinstance(x.get('name'), str)
                     and isinstance(x.get('filterable'), bool) for x in reports))
    res.check(shape, f'estructura correcta para la empresa "{data.get("empresa_nombre")}"',
              f'{len(reports or [])} reportes', 'faltan campos (id:int, name:str, filterable:bool)')
    for x in reports or []:
        print(f'          - id={x.get("id")}  {x.get("name")}  filterable={x.get("filterable")}')
    if not reports:
        res.warn('la empresa no tiene reportes privados asociados', 'no hay nada que probar en /report-config')
    if negative:
        r = api.call('GET', '/reports')
        res.check(r.status_code == 401, 'sin token -> 401', detail_fail=f'HTTP {r.status_code}')
        r = api.call('GET', '/reports', 'token.falso.de-prueba')
        res.check(r.status_code == 401, 'token falso -> 401', detail_fail=f'HTTP {r.status_code}')
    return reports or []


def check_embed_payload(res, cfg):
    """Lo necesario para llamar a powerbi.embed()."""
    needed = {k: cfg.get(k) for k in ('embedUrl', 'reportId', 'accessToken', 'workspaceId')}
    missing = [k for k, v in needed.items() if not (isinstance(v, str) and v)]
    if not res.check(not missing, 'trae embedUrl, reportId, accessToken y workspaceId',
                     detail_fail=f'faltan o estan vacios: {missing}'):
        return
    url = cfg['embedUrl']
    res.check(url.startswith('https://') and 'reportEmbed' in url, 'embedUrl es una URL de reportEmbed de Power BI',
              detail_fail=f'forma inesperada: {url[:60]}')
    payload = jwt_payload(cfg['accessToken'])
    if payload is None:
        res.warn('el accessToken no parece un JWT', 'no se pudo comprobar su vigencia')
        return
    minutes = (payload.get('exp', 0) - time.time()) / 60
    res.check(minutes > 0, 'el accessToken de Power BI esta vigente', f'vence en {minutes:.0f} min',
              'ya esta vencido')
    if 0 < minutes < 5:
        res.warn('el accessToken vence en menos de 5 minutos', 'pedilo justo antes de cada render')
    if 'powerbi' not in str(payload.get('aud', '')).lower():
        res.warn('la audiencia del token no menciona powerbi', f'aud={payload.get("aud")}')


def check_actions(res, cfg, legacy):
    """Bloques nuevos del hotfix: settings y actions."""
    settings, actions = cfg.get('settings'), cfg.get('actions')
    if settings is None and actions is None:
        if legacy:
            res.warn('sin settings/actions', 'esperado con --legacy (servidor sin el hotfix)')
        else:
            res.fail('trae settings y actions', 'faltan; si el servidor aun no tiene el hotfix usa --legacy')
        return
    flags_ok = (isinstance(settings, dict) and isinstance(settings.get('persistentFiltersEnabled'), bool)
                and isinstance(actions, dict) and isinstance(actions.get('resetToDefault'), bool)
                and isinstance(actions.get('refreshVisuals'), bool))
    if not res.check(flags_ok, 'settings.persistentFiltersEnabled y actions.{resetToDefault, refreshVisuals} son booleanos',
                     f'reset={actions.get("resetToDefault") if flags_ok else "?"}, '
                     f'refreshVisuals={actions.get("refreshVisuals") if flags_ok else "?"}',
                     f'forma inesperada: settings={settings!r} actions={actions!r}'):
        return
    res.check(settings['persistentFiltersEnabled'] == actions['resetToDefault'],
              'persistentFiltersEnabled coincide con resetToDefault (el SDK lo necesita para restablecer)',
              detail_fail='difieren: el boton de restablecer no funcionaria')
    if 'refreshDataset' in actions:
        res.fail('la API privada no debe exponer refreshDataset', 'el refresh del modelo es solo de links publicos')


def test_report_config(api, res, token, report, args):
    rid = report['id']
    print(f'\n[3] GET /report-config?report_id={rid}   ({report["name"]})')
    r = api.call('GET', '/report-config', token, params={'report_id': rid})
    cfg = body(r)
    if not res.check(r.status_code == 200, 'configuracion de embed', detail_fail=f'HTTP {r.status_code}: {cfg.get("error", r.text[:120])}'):
        return
    check_embed_payload(res, cfg)
    check_actions(res, cfg, args.legacy)

    if args.filter:
        r2 = api.call('GET', '/report-config', token, params={'report_id': rid, 'filter': args.filter})
        url2 = body(r2).get('embedUrl', '')
        if report['filterable']:
            applied = r2.status_code == 200 and 'filter=' in url2 and url2 != cfg.get('embedUrl')
            if applied:
                res.ok(f'filtro "{args.filter}" aplicado en embedUrl')
            else:
                res.warn(f'filtro "{args.filter}" no se aplico', f'HTTP {r2.status_code}; revisa filter_table y filter_column del reporte')
        else:
            res.check(r2.status_code == 200 and 'filter=' not in url2, 'reporte no filtrable: el servidor ignora el filtro',
                      detail_fail='aplico un filtro a un reporte que no lo permite')
    elif report['filterable']:
        print('          (reporte filtrable: usa --filter VALOR para probar el filtro)')


def test_config_errors(api, res, token):
    print('\n[4] Errores de /report-config')
    r = api.call('GET', '/report-config', params={'report_id': 1})
    res.check(r.status_code == 401, 'sin token -> 401', detail_fail=f'HTTP {r.status_code}')
    r = api.call('GET', '/report-config', token)
    res.check(r.status_code == 400, 'sin report_id -> 400', detail_fail=f'HTTP {r.status_code}')
    r = api.call('GET', '/report-config', token, params={'report_id': 2147483647})
    res.check(r.status_code == 404, 'reporte inexistente -> 404', detail_fail=f'HTTP {r.status_code}')


# -- main -------------------------------------------------------------------

def parse_args(argv):
    p = argparse.ArgumentParser(description='Prueba la API privada de Sudata PBI.')
    p.add_argument('--base', help='URL base, ej. https://reports.sudata.co/private (por defecto API_BASE)')
    p.add_argument('--env-file', default=str(HERE / '.env'), help='archivo con API_BASE, CLIENT_ID y CLIENT_SECRET')
    p.add_argument('--report-id', type=int, help='probar solo este reporte')
    p.add_argument('--filter', help='valor de filtro a probar en los reportes filtrables')
    p.add_argument('--legacy', action='store_true', help='servidor sin el hotfix: no exigir settings/actions')
    p.add_argument('--no-negative', action='store_true', help='omitir las pruebas de errores (credenciales falsas, sin token)')
    return p.parse_args(argv)


def main(argv=None):
    sys.stdout.reconfigure(errors='replace')
    args = parse_args(argv)
    base, client_id, client_secret, env_found = resolve_config(args)
    problems = []
    if not base:
        problems.append('falta API_BASE')
    if client_id in PLACEHOLDERS:
        problems.append('falta CLIENT_ID (o es el valor de ejemplo)')
    if client_secret in PLACEHOLDERS:
        problems.append('falta CLIENT_SECRET (o es el valor de ejemplo)')
    if problems:
        print('Configuracion incompleta: ' + '; '.join(problems) + f'.\nDefinilas en {args.env_file} o como variables de entorno.')
        return 2

    print('Sudata PBI - prueba de la API privada')
    print(f'Base   : {base}')
    print(f'Cliente: {mask(client_id)}   secret: ********   (config: {"archivo .env" if env_found else "variables de entorno"})')
    if args.legacy:
        print('Modo   : --legacy (no se exigen settings/actions)')

    api, res = Api(base), Results()
    token = test_login(api, res, client_id, client_secret, not args.no_negative)
    if token:
        reports = test_reports(api, res, token, not args.no_negative)
        if args.report_id is not None:
            reports = [x for x in reports if x['id'] == args.report_id]
            if not reports:
                res.fail(f'el reporte {args.report_id} esta en la lista de la empresa')
        for report in reports:
            test_report_config(api, res, token, report, args)
        if not args.no_negative:
            test_config_errors(api, res, token)

    print(f'\nResumen: {res.passed} ok, {res.failed} fallas, {res.warned} avisos')
    if not res.failed:
        print('Recordatorio: Restablecer y Actualizar visuales se ejecutan en el navegador; probalos con "python app.py".')
    return 1 if res.failed else 0


if __name__ == '__main__':
    sys.exit(main())
