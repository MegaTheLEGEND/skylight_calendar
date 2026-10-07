"""Offline regression tests for Skylight domain-service authorization.

Run from the repository root:
    python3 -m unittest discover -s tests -p 'test_service_authorization.py' -v

The real registration, guard, resolver and action functions are loaded directly
from the integration's AST, without importing Home Assistant or the vendor API.
Test calls represent already validated ServiceCall data. HA dispatch, schemas,
permission-policy implementation, filesystem and vendor operations are stubbed.
"""
from __future__ import annotations
import ast
from datetime import date, datetime
import logging
import mimetypes
from pathlib import Path
from types import SimpleNamespace as NS
import unittest

SOURCE = Path(__file__).resolve().parents[1]
EXPECTED_METHODS = {
    'create_chore': 'create_chores', 'create_task': 'create_task_box_item',
    'create_list': 'create_list', 'delete_list': 'delete_list',
    'create_reward': 'create_reward', 'redeem_reward': 'redeem_reward',
    'create_recipe': 'create_recipe', 'plan_meal': 'create_meal_sitting',
    'add_recipe_to_grocery_list': 'add_recipe_to_grocery_list',
    'upload_media': 'upload_media',
}

class HomeAssistantError(Exception):
    def __init__(self, *args, **kwargs): super().__init__(*args)
class Unauthorized(HomeAssistantError): pass
class UnknownUser(Unauthorized): pass
class SkylightAPIError(Exception): pass


def load_exact_nodes():
    target = SOURCE / 'custom_components/skylight/__init__.py'
    tree = ast.parse(target.read_text())
    function_names = {'_resolve_entry', '_resolve_assignees', '_async_authorize_frame_write', '_make_write_handler', '_async_register_services', '_create_chore', '_create_task', '_create_list', '_delete_list', '_create_reward', '_redeem_reward', '_create_recipe', '_plan_meal', '_add_recipe_to_grocery'}
    nodes = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in function_names:
            nodes.append(node)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(t, ast.Name) and (t.id.startswith('SERVICE_') or t.id in {'_ALLOWED_EXTS', '_WRITE_SERVICES', '_ALL_SERVICES'}) for t in targets):
                nodes.append(node)
    future = ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)
    module = ast.fix_missing_locations(ast.Module(body=[future, *nodes], type_ignores=[]))
    scope = {'DOMAIN': 'skylight', 'POLICY_CONTROL': 'control', 'HomeAssistantError': HomeAssistantError, 'Unauthorized': Unauthorized, 'UnknownUser': UnknownUser, 'SkylightAPIError': SkylightAPIError, 'date': date, 'dt_util': NS(now=lambda: datetime(2025,1,1)), 'mimetypes': mimetypes, '_LOGGER': logging.getLogger('offline')}
    for node in ast.walk(module):
        if isinstance(node, ast.Name) and node.id.endswith('_SCHEMA'):
            scope[node.id] = object()
    exec(compile(module, str(target), 'exec'), scope)
    return scope


class Rig:
    def __init__(self, scope, *, uid='reader', active=True, admin=False, allowed=(), registry=True, two_frames=False):
        self.log = []
        self.scope = dict(scope)
        self.uid = uid
        self.registry_queries = []
        log = self.log
        class API:
            def __getattr__(self, method):
                async def invoke(*args, **kwargs):
                    log.append(('api', method, args, kwargs))
                    return {'data': [{'id':'fake-person', 'attributes': {'label':'Alex', 'linked_to_profile':True}}]} if method == 'get_categories' else {'data': {'message_ids': ['fake']}}
                return invoke
        class Coordinator:
            async def async_request_refresh(self): log.append(('refresh',))
        class Permissions:
            def check_entity(self, entity, policy):
                log.append(('permission', entity, policy))
                return entity in allowed and policy == 'control'
        async def get_user(user_id):
            log.append(('user', user_id))
            return None if user_id == 'unknown' else NS(is_active=active, is_admin=admin, permissions=Permissions())
        rig = self
        class Registry:
            def async_get_entity_id(self, domain, platform, unique):
                rig.registry_queries.append((domain, platform, unique))
                return {'skylight_frame_a_calendar': 'calendar.a', 'skylight_frame_b_calendar': 'calendar.b'}.get(unique) if registry else None
        class FakePath:
            def __init__(self, value): log.append(('file', 'construct')); self.value = value
            def expanduser(self): log.append(('file', 'expand')); return self
            def is_file(self): log.append(('file', 'stat')); return True
            @property
            def suffix(self): log.append(('file', 'suffix')); return '.jpg'
            @property
            def name(self): return 'fake.jpg'
            def __str__(self): return '/fake.jpg'
            def read_bytes(self): log.append(('file', 'read')); return b'fake'
        class Services:
            def __init__(self): self.handlers = {}; self.schemas = {}
            def has_service(self, domain, name): return (domain, name) in self.handlers
            def async_register(self, domain, name, handler, schema):
                self.handlers[domain, name] = handler; self.schemas[domain, name] = schema
        def allowed_path(path): log.append(('file', 'allowlist')); return True
        async def executor(fn): log.append(('executor',)); return fn()
        entry = lambda frame: {'frame_id': frame, 'api': API(), 'sensor_coordinator': Coordinator(), 'lists_coordinator': Coordinator(), 'photos_coordinator': Coordinator()}
        entries = {'a': entry('frame_a')}
        if two_frames: entries['b'] = entry('frame_b')
        self.hass = NS(data={'skylight': entries}, auth=NS(async_get_user=get_user), services=Services(), config=NS(is_allowed_path=allowed_path), async_add_executor_job=executor)
        self.scope.update(er=NS(async_get=lambda _: Registry()), Path=FakePath)
        # Functions reference their original exec globals, so re-execute their
        # unchanged exact AST code objects into this rig's isolated namespace.
        import types
        for name, value in scope.items():
            if isinstance(value, types.FunctionType):
                self.scope[name] = types.FunctionType(value.__code__, self.scope, name, value.__defaults__, value.__closure__)
        self.scope['_WRITE_SERVICES'] = tuple((name, schema, key, self.scope[action.__name__]) for name, schema, key, action in scope['_WRITE_SERVICES'])
        self.scope['_async_register_services'](self.hass)

    async def call(self, service, frame=None):
        data = {'summary':'offline', 'routine':False, 'up_for_grabs':False, 'label':'offline', 'kind':'to_do', 'list_id':'list', 'name':'reward', 'point_value':1, 'respawn_on_redemption':False, 'reward_id':'reward', 'date':date(2025,1,1), 'meal_category_id':'meal', 'recipe_id':'recipe', 'file_path':'/fake.jpg', 'assignees':['Alex']}
        if frame is not None: data['frame_id'] = frame
        # Nonempty assignees force the pre-write categories API path.
        await self.hass.services.handlers['skylight', service](NS(service=service, data=data, context=NS(user_id=self.uid)))

    def effects(self): return [x for x in self.log if x[0] in {'api', 'refresh', 'file', 'executor'}]


class RegistrationTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.scope = load_exact_nodes()

    def test_registration_inventory_and_idempotence(self):
        rig = Rig(self.scope)
        self.assertEqual(set(name for domain, name in rig.hass.services.handlers), set(EXPECTED_METHODS))
        self.assertEqual(len(self.scope['_WRITE_SERVICES']), 9)
        before = dict(rig.hass.services.handlers)
        rig.scope['_async_register_services'](rig.hass)
        self.assertEqual(before, rig.hass.services.handlers)
        self.assertEqual(len(rig.hass.services.schemas), 10)

    def test_registry_key_uses_actual_aggregate_calendar_constructor(self):
        tree = ast.parse((SOURCE / 'custom_components/skylight/calendar.py').read_text())
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'SkylightAggregateCalendar')
        assignments = [n for n in ast.walk(cls) if isinstance(n, ast.Assign) and any(isinstance(t, ast.Attribute) and t.attr == '_attr_unique_id' for t in n.targets)]
        self.assertEqual(len(assignments), 1)
        key = eval(compile(ast.Expression(assignments[0].value), '<actual calendar.py>', 'eval'), {'frame_id':'frame_a'})
        self.assertEqual(key, 'skylight_frame_a_calendar')



def add_case(service, scenario, kwargs, denied):
    async def test(self):
        rig = Rig(self.scope, **kwargs)
        if denied:
            with self.assertRaises(denied): await rig.call(service, 'frame_b' if scenario == 'wrong_frame' else None)
            self.assertEqual(rig.effects(), [], rig.log)
        else:
            await rig.call(service)
            calls = [x[1] for x in rig.log if x[0] == 'api']
            self.assertIn(EXPECTED_METHODS[service], calls)
            for effect in (x for x in rig.log if x[0] == 'api'):
                if service == 'upload_media':
                    self.assertEqual(effect[3]['frame_ids'], ['frame_a'])
                else:
                    self.assertEqual(effect[2][0], 'frame_a')
            self.assertEqual(sum(x[0] == 'refresh' for x in rig.log), 1)
            if kwargs.get('uid', 'reader') is None:
                self.assertFalse(any(x[0] in {'user', 'permission'} for x in rig.log))
            else:
                self.assertEqual(rig.log[0][0], 'user')
                if service != 'upload_media':
                    self.assertEqual(rig.log[1], ('permission','calendar.a','control'))
                    self.assertEqual(rig.registry_queries, [('calendar','skylight','skylight_frame_a_calendar')])
                else:
                    self.assertIn(('file','read'), rig.log)
        if scenario == 'missing_registry':
            self.assertEqual(rig.registry_queries, [('calendar','skylight','skylight_frame_a_calendar')])
    test.__name__ = f'test_{service}_{scenario}'
    setattr(RegistrationTests, test.__name__, test)

for service in EXPECTED_METHODS:
    for scenario, kwargs, denied in [
        ('unknown_user', {'uid':'unknown'}, UnknownUser),
        ('inactive_user', {'active':False, 'admin':True, 'allowed':['calendar.a']}, Unauthorized),
        ('readonly_user', {}, Unauthorized),
        ('trusted_automation', {'uid':None}, None),
    ]: add_case(service, scenario, kwargs, denied)
    if service == 'upload_media':
        add_case(service, 'control_without_admin', {'allowed':['calendar.a']}, Unauthorized)
        add_case(service, 'owner_admin', {'uid':'owner', 'admin':True}, None)
    else:
        for scenario, kwargs, denied in [
            ('correct_frame', {'allowed':['calendar.a']}, None),
            ('wrong_frame', {'allowed':['calendar.a'], 'two_frames':True}, Unauthorized),
            ('missing_registry', {'allowed':['calendar.a'], 'registry':False}, Unauthorized),
        ]: add_case(service, scenario, kwargs, denied)

if __name__ == '__main__': unittest.main(verbosity=2)
