"""Owned synthetic Linux18 role/metadata tests, never a real profile or DB."""
from copy import deepcopy
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from opennavicat import navicat_linux as source
from opennavicat import navicat_linux_json as streaming
from opennavicat import navicat_profiles as profiles
from opennavicat.navicat_linux_native import discover_native_linux_profiles
from opennavicat.storage import Store

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='Linux dirfd reader requires POSIX')
TYPES = {'PGSQL':'postgresql','SQLITE':'sqlite','MYSQL':'mysql','MARIADB':'mariadb',
         'MSSQL':'sqlserver','MONGODB':'mongodb','REDIS':'redis'}


@pytest.fixture(autouse=True)
def isolated_secrets(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('A synthetic discovery test accessed the host credential store')
    monkeypatch.setitem(sys.modules,'keyring',SimpleNamespace(
        get_password=forbidden,set_password=forbidden,delete_password=lambda *args:None))


def row(kind='PGSQL', name='Generated connection', **changes):
    value = dict(ConnType=kind,Name=name,ConnectionUUID='',Profiles=[],ActiveServerSettings='',
                 UnknownJsonPropertiesString='',Username='generated',UseSSH=False)
    if kind == 'SQLITE':
        value.update(DatabaseFile='/generated/example.sqlite3',IsEncrypted=False,UseHTTP=False)
        value.pop('UseSSH')
    else:
        value.update(ServiceProvider='Default',Hostname='generated.invalid',Port=5432,UseSSL=False)
        if kind in ('PGSQL','MSSQL'):value['InitialDatabase']='generated'
        if kind in ('MYSQL','MARIADB','PGSQL'):value['UseHTTP']=False
        if kind == 'MSSQL':
            value.pop('UseSSL');value.update(AuthType='SQLServer',UseEncryption=True,TrustServerCertificate=False)
        elif kind == 'MONGODB':
            value.update(Username='',AuthMechanism='None',AuthSource='',ConnMethod='Standalone',UseSRVRecord=False,
                         RetryReads=True,RetryWrites=True,ReadPreference='Primary',MemberList=[])
        elif kind == 'REDIS':value.update(AuthenticationMode='None',Type='Standalone',Username='default',Role='Master')
    value.update(changes)
    return value


def document(rows, user='generated-user', project='generated-project'):
    return {'Users':[{'UserUUID':user,'Projects':[{'Name':'Generated project','ProjectUUID':project,'Servers':rows}]}]}


def grouped(*members):
    return {'version':'1.1','connections':[],'vgroups':[{'vgroup_name':'Generated group','vgroup_type':'CONNECTION',
            'items':[{'name':name,'type':'CONNECTION','server_type':kind} for kind,name in members]}]}


@pytest.fixture
def files(tmp_path, monkeypatch):
    home=tmp_path.resolve()/'home';home.mkdir()
    monkeypatch.setattr(Path,'home',classmethod(lambda cls:home));monkeypatch.delenv('XDG_CONFIG_HOME',raising=False)
    conn=home/'.config/navicat/Common/connections.json';conn.parent.mkdir(parents=True)
    group=home/'.config/navicat/Premium/Profiles/vgroup.json';group.parent.mkdir(parents=True)
    def write(rows=None, *, data=None, groups=None):
        conn.write_text(json.dumps(document([row()] if rows is None else rows) if data is None else data))
        if groups is None:
            group.unlink(missing_ok=True)
        else:group.write_text(json.dumps(groups))
        return discover_native_linux_profiles(home=home,environ={})
    return home,conn,group,write


def test_all_seven_observed_families_display_only_and_never_connect(files,monkeypatch):
    from opennavicat.database import DatabaseSession
    def denied(*a,**kw):pytest.fail('DB or credential API invoked by discovery')
    monkeypatch.setattr(DatabaseSession,'connect',denied)
    for function in ('_convert','_load','_records'):monkeypatch.setattr(profiles,function,denied)
    _,_,_,write=files
    found=write([row(k,name=f'Generated {k}') for k in TYPES])
    assert len(found.profiles)==7 and {p.config.driver for p in found.profiles}==set(TYPES.values())
    assert found.blocked_count==7 and all(p.config_complete for p in found.profiles)
    assert all(p.source_format=='linux-native-json' and p.blocked for p in found.profiles)
    assert all(p.config.password==p.config.ssh_password==p.config.ssl_key_password==p.config.ssh_key_passphrase=='' for p in found.profiles)
    assert all('Generated' not in repr(p) and '.invalid' not in repr(p) for p in found.profiles)
    assert all('linux_native_settings' in p.unsupported_fields for p in found.profiles)


def test_default_native_route_matches_explicit_safe_reader(files):
    _,_,_,write=files
    direct=write()
    default=profiles.discover_navicat_profiles(platform='linux')
    assert direct.profiles==default.profiles and direct.issues==default.issues


def test_secrets_and_opaque_strings_are_never_decoded_or_retained(files,monkeypatch,tmp_path):
    _,conn,_,write=files
    forbidden=['PASSWORD-CANARY','KEY-CANARY','BLOB-CANARY','PATH-CANARY']
    original=json.loads
    decoded=[]
    def loads(token,*a,**kw):
        text=bytes(token).decode('utf8') if isinstance(token,(bytes,bytearray)) else token
        decoded.append(text)
        assert not any(canary in text for canary in forbidden)
        return original(token,*a,**kw)
    monkeypatch.setattr(streaming.json,'loads',loads)
    r=row(Password=forbidden[0],SSL={'PrivateKey':forbidden[1]},SSH={'Password':forbidden[0]},
          UnknownJsonPropertiesString=forbidden[2],SettingsSavePath=forbidden[3])
    found=write([r])
    assert len(found.profiles)==1 and not found.profiles[0].config_complete
    assert 'UnknownJsonPropertiesString' in found.profiles[0].unsupported_fields
    store=Store(tmp_path/'workspace');store.sync_navicat_profiles(found)
    public=repr(found)+store.path.read_text()
    assert all(canary not in public for canary in forbidden)
    assert decoded and not any('Password' in token for token in decoded)


@pytest.mark.parametrize('change',[
    {'UseSSL':True},{'UseSSL':'false'},{'UseSSH':True},{'UseSSH':None},{'UseHTTP':True},
    {'Profiles':[{'Password':'SYNTHETIC-OPAQUE'}]},{'ActiveServerSettings':'alternate'},
    {'InitialSessionQueries':'SET something=1'},{'ConnectionParameters':'opaque-option'},
    {'UnknownJsonPropertiesString':'opaque-option'},{'FutureAuth':{'Password':'opaque'}},
    {'Hostname':True},{'Port':True},{'Port':0},{'Port':65536},{'Port':1.5},
    {'Username':{}},{'InitialDatabase':False},{'AuthType':'Cross-family auth'},
    {'Hostname':'postgres://user:pw@host/db'},{'Hostname':'host/path'},{'Hostname':'host\x00tail'},
])
def test_unknown_or_incomplete_settings_visible_but_preserve_last_known_config(files,tmp_path,change):
    _,_,_,write=files
    initial=write();store=Store(tmp_path/'workspace');store.sync_navicat_profiles(initial)
    before=deepcopy(store.state['connections'])
    current=write([row(**change)])
    assert len(current.profiles)==1 and current.profiles[0].blocked and not current.profiles[0].config_complete
    store.sync_navicat_profiles(current)
    assert store.state['connections']==before
    # A complete later source can update the same unreviewed logical row.
    recovered=write([row(Hostname='recovered.invalid')]);store.sync_navicat_profiles(recovered)
    assert len(store.connections())==1 and store.connections()[0].host=='recovered.invalid'


@pytest.mark.parametrize('missing',['Name','Hostname','Port','Username','InitialDatabase','ConnectionUUID','Profiles','ActiveServerSettings','UseSSL','UseSSH','UseHTTP'])
def test_missing_required_record_fields_are_not_authoritative_empty_values(files,missing):
    _,_,_,write=files;r=row();r.pop(missing)
    found=write([r]);assert len(found.profiles)==1 and not found.profiles[0].config_complete


@pytest.mark.parametrize('changes',[{'IsEncrypted':True},{'DatabaseFile':'relative.sqlite3'},{'DatabaseFile':'//share/db'},
                                    {'DatabaseFile':'file:/tmp/a?mode=rw'},{'AttachedDatabases':[{'Password':'opaque'}]}])
def test_sqlite_unsafe_or_unknown_modes_cannot_become_plain_local_connections(files,changes):
    *_,write=files
    found=write([row('SQLITE',**changes)])
    assert not found.profiles[0].config_complete and found.profiles[0].blocked
    if changes.get('IsEncrypted') or 'DatabaseFile' in changes:assert found.profiles[0].config.database==''


def test_stable_ids_do_not_depend_on_route_restart_record_order_or_other_namespaces(files):
    home,_,_,write=files
    first=write([row('PGSQL','Same'),row('SQLITE','Other')]);byname={p.config.name:p.source_id for p in first.profiles}
    again=write([row('SQLITE','Other'),row('PGSQL','Same')])
    assert {p.config.name:p.source_id for p in again.profiles}==byname
    routed=discover_native_linux_profiles(home=home,environ={'XDG_CONFIG_HOME':str(home/'.config')})
    assert {p.config.name:p.source_id for p in routed.profiles}==byname
    different=document([row('PGSQL','Same')]);different['Users'].append(document([row('PGSQL','Same')],user='other-user')['Users'][0])
    scoped=write(data=different);assert len(scoped.profiles)==2 and len({p.source_id for p in scoped.profiles})==2
    different['Users'][0]['Projects'].append(document([row('PGSQL','Same')],project='other-project')['Users'][0]['Projects'][0])
    scoped=write(data=different);assert len(scoped.profiles)==3 and len({p.source_id for p in scoped.profiles})==3


def test_nonempty_connection_uuid_is_scoped_not_a_global_merge(files):
    *_,write=files;data=document([row(ConnectionUUID='native-id',name='First')])
    data['Users'].append(document([row(ConnectionUUID='native-id',name='Second')],user='another-user')['Users'][0])
    found=write(data=data)
    assert len(found.profiles)==2 and found.profiles[0].source_id!=found.profiles[1].source_id


def test_duplicate_records_remain_visible_without_losing_previous_identity_or_review(files,tmp_path):
    *_,write=files;store=Store(tmp_path/'workspace')
    first=write();store.sync_navicat_profiles(first);identifier=store.connections()[0].id
    local=replace(store.connections()[0],host='local.invalid');store.save_connection(local)
    current=write([row(),row(Hostname='conflict.invalid')])
    assert len(current.profiles)==2 and len({p.source_id for p in current.profiles})==2
    assert first.profiles[0].source_id==current.profiles[0].source_id
    assert all(not p.config_complete and p.blocked for p in current.profiles)
    store.sync_navicat_profiles(current);again=store.sync_navicat_profiles(current)
    assert len(store.connections())==2 and again['added']==0
    assert next(c for c in store.connections() if c.id==identifier).host=='local.invalid'
    assert not store.state['navicat_imports'][identifier]['blocked']


def test_missing_namespace_identity_keeps_rows_visible_but_cannot_overwrite_lkg(files,tmp_path):
    *_,write=files;first=write();store=Store(tmp_path/'workspace');store.sync_navicat_profiles(first)
    data=document([row(Hostname='uncertain.invalid')],user='')
    found=write(data=data)
    assert len(found.profiles)==1 and not found.profiles[0].config_complete
    store.sync_navicat_profiles(found)
    assert any(c.host=='generated.invalid' for c in store.connections())
    assert not any(m['source_missing'] for m in store.state['navicat_imports'].values())


@pytest.mark.parametrize('data',[{},[],{'Users':{}},{'Users':[None]}, {'Users':[{'UserUUID':'x','Projects':None}]},
                                {'PostgreSQL':{'Name':{'connection_uuid':'do-not-map','host':'opaque.invalid'}}}])
def test_unaccepted_role_shapes_never_fall_back_to_mac_or_claim_empty_source(files,data):
    *_,write=files
    found=write(data=data)
    assert not found.profiles and 'unsupported_source_structure' in {i.code for i in found.issues}


def test_valid_empty_role_snapshot_can_mark_missing_without_deleting_existing_rows(files,tmp_path):
    *_,write=files;store=Store(tmp_path/'workspace');store.sync_navicat_profiles(write())
    empty=write(data={'Users':[]});store.sync_navicat_profiles(empty)
    assert len(store.connections())==1 and next(iter(store.state['navicat_imports'].values()))['source_missing']


def test_group_known_memberships_then_failures_preserve_lkg_and_valid_empty_clears(files,tmp_path):
    _,_,group,write=files;store=Store(tmp_path/'workspace')
    first=write(groups=grouped(('PGSQL','Generated connection')))
    assert first.profiles[0].group_paths==(('Generated group',),) and first.profiles[0].groups_complete
    store.sync_navicat_profiles(first)
    for payload in (None,'{not valid',json.dumps({'version':'unknown','connections':[],'vgroups':[]})):
        if payload is None:group.unlink(missing_ok=True)
        else:group.write_text(payload)
        current=profiles.discover_navicat_profiles(platform='linux')
        assert not current.profiles[0].groups_complete
        store.sync_navicat_profiles(current)
        assert next(iter(store.state['navicat_imports'].values()))['group_paths']==[['Generated group']]
    empty=write(groups={'version':'1.1','connections':[],'vgroups':[]});store.sync_navicat_profiles(empty)
    assert next(iter(store.state['navicat_imports'].values()))['group_paths']==[]


def test_fresh_mixed_groups_keep_known_paths_but_not_ambiguous_or_unknown_members(files,tmp_path):
    *_,write=files;groups=grouped(('PGSQL','Generated connection'))
    groups['vgroups'][0]['items'].append({'name':'Unknown','type':'FUTURE','server_type':'PGSQL'})
    found=write(groups=groups)
    assert found.profiles[0].group_paths==(('Generated group',),) and not found.profiles[0].groups_complete
    store=Store(tmp_path/'workspace');store.sync_navicat_profiles(found)
    assert next(iter(store.state['navicat_imports'].values()))['group_paths']==[['Generated group']]
    data=document([row()]);data['Users'][0]['Projects'].append(document([row()],project='another-project')['Users'][0]['Projects'][0])
    ambiguous=write(data=data,groups=grouped(('PGSQL','Generated connection')))
    assert len(ambiguous.profiles)==2 and all(not p.group_paths and not p.groups_complete for p in ambiguous.profiles)
    assert 'ambiguous_group_members' in {i.code for i in ambiguous.issues}


def test_group_links_unknown_fields_do_not_turn_a_partial_source_into_empty_authority(files,tmp_path):
    _,_,group,write=files;store=Store(tmp_path/'workspace');store.sync_navicat_profiles(write(groups=grouped(('PGSQL','Generated connection'))))
    outside=group.with_name('outside.json');outside.write_text(json.dumps(grouped()))
    group.unlink();group.symlink_to(outside)
    found=profiles.discover_navicat_profiles(platform='linux');store.sync_navicat_profiles(found)
    assert not found.profiles[0].groups_complete
    assert next(iter(store.state['navicat_imports'].values()))['group_paths']==[['Generated group']]


@pytest.mark.parametrize('race',['replace','rewrite','delete','parent'])
def test_full_projection_file_and_parent_binding_rejects_changes_after_parse(files,monkeypatch,race):
    _,conn,_,write=files;write()
    original=streaming.project_json
    def changed(stream,spec,**kwargs):
        value=original(stream,spec,**kwargs)
        if race=='replace':
            other=conn.with_suffix('.new');other.write_text('{}');other.replace(conn)
        elif race=='rewrite':conn.write_text('{"Users":[]}')
        elif race=='delete':conn.unlink()
        else:
            parent=conn.parent;parent.rename(parent.with_name('moved'));parent.mkdir();conn.write_text('{}')
        return value
    monkeypatch.setattr(streaming,'project_json',changed)
    found=profiles.discover_navicat_profiles(platform='linux')
    assert not found.profiles and 'source_read_failed' in {i.code for i in found.issues}


@pytest.mark.parametrize('suffix',[b' trailing', b',', b'\xff', b'\n{"duplicateRoot":true}'])
def test_invalid_or_trailing_source_is_not_partially_imported(files,suffix):
    _,conn,_,write=files;write();conn.write_bytes(conn.read_bytes()+suffix)
    found=profiles.discover_navicat_profiles(platform='linux')
    assert not found.profiles and all('duplicateRoot' not in repr(i) for i in found.issues)
    assert 'source_read_failed' in {i.code for i in found.issues}


def test_unknown_driver_retains_visible_review_only_metadata(files):
    *_,write=files
    found=write([row('FUTURE')]);assert len(found.profiles)==1 and found.profiles[0].config.driver=='unknown'
    assert not found.profiles[0].config_complete and found.profiles[0].blocked


def test_local_review_and_secret_cache_favorites_survive_later_native_change(files,tmp_path,monkeypatch):
    *_,write=files;store=Store(tmp_path/'workspace');store.sync_navicat_profiles(write())
    config=replace(store.connections()[0],host='local.invalid',password='GENERATED-LOCAL-SECRET')
    # Keep generated secret in process only and never use host keychain.
    store.save_connection(config,remember=False)
    store.add_favorite('Generated favorite','SELECT 1',config.id)
    before=deepcopy(store.state['connections']);fav=deepcopy(store.state['favorites'])
    store.sync_navicat_profiles(write([row(Hostname='new.invalid')]))
    assert store.state['connections']==before and store.state['favorites']==fav
    assert store.secret(store.connections()[0])=='GENERATED-LOCAL-SECRET'
    assert not store.state['navicat_imports'][config.id]['blocked']
    assert 'GENERATED-LOCAL-SECRET' not in store.path.read_text()


def test_unknown_global_options_make_all_records_incomplete(files,tmp_path):
    *_,write=files;store=Store(tmp_path/'workspace');store.sync_navicat_profiles(write())
    before=deepcopy(store.state['connections']);data=document([row(Hostname='uncertain.invalid')]);data['FutureGlobalSettings']={'Password':'opaque'}
    found=write(data=data);assert len(found.profiles)==1 and not found.profiles[0].config_complete
    store.sync_navicat_profiles(found);assert store.state['connections']==before


@pytest.mark.parametrize('role',['user','project'])
def test_duplicate_namespace_retains_first_identity_and_separate_visible_conflict(files,role):
    *_,write=files;first=write();data=document([row()])
    if role=='user':data['Users'].append(deepcopy(data['Users'][0]))
    else:data['Users'][0]['Projects'].append(deepcopy(data['Users'][0]['Projects'][0]))
    found=write(data=data)
    assert len(found.profiles)==2 and len({p.source_id for p in found.profiles})==2
    assert found.profiles[0].source_id==first.profiles[0].source_id
    assert all(p.blocked and not p.config_complete for p in found.profiles)


@pytest.mark.parametrize('valid_also',[False,True])
def test_group_family_alias_does_not_expand_accepted_linux_enum_or_remove_valid_membership(files,valid_also):
    *_,write=files;groups=grouped(('PGSQL','Generated connection'))
    if not valid_also:groups['vgroups'][0]['items']=[]
    groups['vgroups'][0]['items'].append({'name':'Generated connection','type':'CONNECTION','server_type':'postgres'})
    found=write(groups=groups)
    assert not found.profiles[0].groups_complete
    assert found.profiles[0].group_paths==((('Generated group',),) if valid_also else ())


def test_incomplete_connection_namespace_cannot_authorize_group_reassignment(files,tmp_path):
    *_,write=files;store=Store(tmp_path/'workspace')
    known=grouped(('PGSQL','Generated connection'));store.sync_navicat_profiles(write(groups=known))
    data=document([row()]);data['Users'].append({'UserUUID':'unknown-user','Projects':'unknown-container'})
    other=grouped(('PGSQL','Generated connection'));other['vgroups'][0]['vgroup_name']='Unproven new group'
    found=write(data=data,groups=other)
    assert len(found.profiles)==1 and not found.profiles[0].groups_complete and not found.profiles[0].group_paths
    store.sync_navicat_profiles(found)
    assert next(iter(store.state['navicat_imports'].values()))['group_paths']==[['Generated group']]
