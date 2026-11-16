"""Generated native smart-creation windows; no private evidence or source I/O."""
from copy import deepcopy
import unittest

from soundbox import rekordbox_playlist_plan as plan
from soundbox import rekordbox_playlist_xml as xml


CREATED = '2026-11-11 04:09:33.639 +00:00'
POSITIONED = '2026-11-11 04:09:33.666 +00:00'
DEFINED = '2026-11-11 04:10:00.001 +00:00'


def identity_uuid(number):
    return f'{number:08x}-0000-4000-8000-000000000001'


def node(identity, seq, *, parent='root', attribute=4, deleted=False):
    return dict(ID=str(identity), Seq=seq, Name=f'Existing {identity}', ImagePath=None,
        Attribute=attribute, ParentID=parent, SmartList=(plan.smart_tag_rule(str(identity), ['111']) if attribute==4 else None),
        UUID=identity_uuid(identity), rb_data_status=2 if deleted else 0, rb_local_data_status=0,
        rb_local_deleted=int(deleted), rb_local_synced=1, usn=None, rb_local_usn=7,
        created_at='old-created', updated_at='old-updated')


def fixture():
    # Intentionally not in Seq order; placement must follow prior sibling Seq.
    nodes = [node(60,2,parent='10'), node(10,1,attribute=1), node(50,1,parent='10'),
             node(20,2,attribute=0), node(30,3), node(70,30,parent='10',deleted=True),
             node(80,40,deleted=True)]
    counter = dict(registry_id='localUpdateCount', int_1=41,
                   created_at='counter-created', updated_at='counter-updated', opaque='keep')
    return nodes, counter


def arguments():
    return dict(identity='99',uuid=identity_uuid(99),timestamp=CREATED)


def replay(nodes, counter, changes):
    rows={row['ID']:deepcopy(row) for row in nodes}; current=deepcopy(counter)
    for change in changes:
        if change['table']=='agentRegistry':
            if current!=change['before']:raise AssertionError('Counter chain does not reproduce sequential effects')
            current=deepcopy(change['after'])
        else:
            if rows.get(change['id'])!=change['before']:raise AssertionError('Definition row chain has a stale predecessor')
            if change['after'] is None:rows.pop(change['id'])
            else:rows[change['id']]=deepcopy(change['after'])
    return rows,current


def xml_fixture():
    return b'''<?xml version="1.0" encoding="UTF-8"?>
<MASTER_PLAYLIST Version="3.0.0" AutomaticSync="0" Product="Keep me">
  <PLAYLISTS>
    <NODE Id="A" ParentId="0" Attribute="1" Timestamp="71" Lib_Type="0" CheckType="0" Opaque="preserved"/>
    <NODE Id="32" ParentId="A" Attribute="4" Timestamp="501" Lib_Type="0" CheckType="0"/>
    <NODE Id="3C" ParentId="A" Attribute="4" Timestamp="601" Lib_Type="0" CheckType="0"/>
    <NODE Id="1000000000000000" ParentId="0" Attribute="17" Timestamp="9" Lib_Type="8" CheckType="0"/>
  </PLAYLISTS>
</MASTER_PLAYLIST>
'''


class SmartCreationWindows(unittest.TestCase):
    def test_root_constructor_is_one_append_with_null_rule_and_zero_xml_clock(self):
        nodes,counter=fixture();before=deepcopy((nodes,counter))
        result=plan.create_smart_constructor(nodes,counter,**arguments())
        self.assertFalse(result['source_write_authority'])
        self.assertEqual(result['counter'],{**counter,'int_1':42})
        expected=dict(ID='99',Seq=4,Name='Untitled Intelligent List',ImagePath=None,
            Attribute=4,ParentID='root',SmartList=None,UUID=identity_uuid(99),rb_data_status=0,
            rb_local_data_status=0,rb_local_deleted=0,rb_local_synced=0,usn=None,rb_local_usn=42,
            created_at=CREATED,updated_at=CREATED)
        self.assertEqual(result['playlist'],expected)
        rows,current=replay(nodes,counter,result['changes'])
        self.assertEqual(current,result['counter']);self.assertEqual(rows.pop('99'),expected)
        self.assertEqual(rows,{row['ID']:row for row in nodes})
        self.assertEqual((nodes,counter),before)
        original=xml_fixture();after=xml.append_smart_node(original,'99')
        attributes,_=xml._parse(after)[0][99]
        self.assertEqual((attributes['Attribute'],attributes['ParentId'],attributes['Timestamp']),('4','0','0'))
        for key,value in xml._parse(original)[0].items():
            self.assertEqual(after[xml._parse(after)[0][key][1][0]:xml._parse(after)[0][key][1][1]],
                             original[value[1][0]:value[1][1]])

    def test_folder_constructor_front_window_mutates_only_scoped_metadata_and_keeps_deleted_history(self):
        nodes,counter=fixture();before=deepcopy((nodes,counter))
        clocks={'50':'2026-11-11 04:09:33.670 +00:00','60':'2026-11-11 04:09:33.673 +00:00'}
        result=plan.create_smart_constructor(nodes,counter,parent='10',position_timestamp=POSITIONED,
            sibling_timestamps=clocks,**arguments())
        rows,current=replay(nodes,counter,result['changes'])
        self.assertEqual((result['playlist']['Seq'],result['playlist']['rb_local_usn'],current['int_1']),(1,43,45))
        self.assertEqual((result['playlist']['created_at'],result['playlist']['updated_at']),(CREATED,POSITIONED))
        self.assertIsNone(result['playlist']['SmartList'])
        for identity,sequence,usn in [('50',2,44),('60',3,45)]:
            old=next(row for row in nodes if row['ID']==identity)
            self.assertEqual(rows[identity],{**old,'Seq':sequence,'rb_local_usn':usn,'updated_at':clocks[identity]})
        for old in nodes:
            if old['ID'] not in ('50','60'):self.assertEqual(rows[old['ID']],old)
        self.assertEqual((nodes,counter),before)
        self.assertEqual(current,{**counter,'int_1':45})
        self.assertFalse(result['source_write_authority'])

    def test_definition_is_one_two_count_window_when_constructor_name_stays_unchanged(self):
        nodes,counter=fixture()
        result=plan.create_smart(nodes,counter,name='Untitled Intelligent List',tag_ids=['111'],
            definition_timestamp=DEFINED,**arguments())
        rows,current=replay(nodes,counter,result['changes'])
        self.assertEqual(current['int_1'],44)  # Constructor1 + combined definition2.
        self.assertEqual(rows['99']['Name'],'Untitled Intelligent List')
        self.assertEqual(rows['99']['SmartList'],plan.smart_tag_rule('99',['111']))
        self.assertEqual((rows['99']['created_at'],rows['99']['updated_at']),(CREATED,DEFINED))
        self.assertFalse(result['source_write_authority'])

    def test_changing_name_and_rule_together_does_not_add_two_separate_definition_windows(self):
        nodes,counter=fixture()
        result=plan.create_smart(nodes,counter,name='Desired smart list',tag_ids=['111','222'],
            definition_timestamp=DEFINED,**arguments())
        rows,current=replay(nodes,counter,result['changes'])
        self.assertEqual(current,{**counter,'int_1':44})
        self.assertEqual(rows['99']['rb_local_usn'],44)
        self.assertEqual(rows['99']['Name'],'Desired smart list')
        self.assertIn('LogicalOperator="2"',rows['99']['SmartList'])
        self.assertEqual([change['after']['int_1'] for change in result['changes'] if change['table']=='agentRegistry'],[42,44])

    def test_folder_complete_creation_is_constructor_n_plus_two_then_one_definition_two(self):
        nodes,counter=fixture()
        result=plan.create_smart(nodes,counter,name='Folder smart list',tag_ids=['111','222'],parent='10',
            position_timestamp=POSITIONED,sibling_timestamps={'50':POSITIONED,'60':POSITIONED},
            definition_timestamp=DEFINED,**arguments())
        rows,current=replay(nodes,counter,result['changes'])
        self.assertEqual((rows['99']['Seq'],rows['99']['rb_local_usn'],current['int_1']),(1,47,47))
        self.assertEqual(rows['50']['rb_local_usn'],44);self.assertEqual(rows['60']['rb_local_usn'],45)
        self.assertEqual(rows['70'],next(row for row in nodes if row['ID']=='70'))
        self.assertEqual(rows['10'],next(row for row in nodes if row['ID']=='10'))
        self.assertEqual([change['after']['int_1'] for change in result['changes'] if change['table']=='agentRegistry'],[45,47])

    def test_folder_parent_xml_touch_uses_independent_clock_and_never_touches_old_child_xml(self):
        nodes,counter=fixture()
        result=plan.create_smart(nodes,counter,name='Folder smart list',tag_ids=['111'],parent='10',
            position_timestamp=POSITIONED,sibling_timestamps={'50':POSITIONED,'60':POSITIONED},
            definition_timestamp=DEFINED,**arguments())
        original=xml_fixture()
        candidate=xml.append_smart_node(original,'99','10',timestamp=900003)
        candidate=xml.touch(candidate,'10',700001)
        xml.validate_transition(original,candidate,result['changes'],touched_playlists=('10',),smart_created=('99',))
        attrs=xml._parse(candidate)[0]
        self.assertEqual(attrs[10][0]['Timestamp'],'700001')
        self.assertEqual(attrs[99][0]['Timestamp'],'900003')
        for identity in (50,60,2**60):
            oldattrs,oldspan=xml._parse(original)[0][identity]
            newattrs,newspan=attrs[identity]
            self.assertEqual(dict(newattrs),dict(oldattrs))
            self.assertEqual(candidate[newspan[0]:newspan[1]],original[oldspan[0]:oldspan[1]])

    def test_unproven_empty_mixed_parent_and_cloud_placement_fail_without_mutating_inputs(self):
        nodes,counter=fixture()
        cases=[]
        cases.append([row for row in nodes if row['ParentID']!='10'])
        mixed=deepcopy(nodes);next(row for row in mixed if row['ID']=='50')['Attribute']=0;cases.append(mixed)
        cloud=deepcopy(nodes);next(row for row in cloud if row['ID']=='50')['usn']=1;cases.append(cloud)
        bad_parent=deepcopy(nodes);next(row for row in bad_parent if row['ID']=='10')['rb_local_deleted']=1;cases.append(bad_parent)
        for current in cases:
            before=deepcopy(current)
            with self.subTest(current=current),self.assertRaises(ValueError):
                plan.create_smart_constructor(current,counter,parent='10',position_timestamp=POSITIONED,
                    sibling_timestamps={'50':POSITIONED,'60':POSITIONED},**arguments())
            self.assertEqual(current,before)
        with self.assertRaises(ValueError):
            plan.create_smart_constructor(nodes,counter,parent='10',position_timestamp=POSITIONED,
                sibling_timestamps={'50':POSITIONED},**arguments())

    def test_retained_identity_collision_and_counter_exhaustion_refuse(self):
        nodes,counter=fixture()
        for changed in ({'identity':'70'},{'uuid':identity_uuid(70)}):
            with self.subTest(changed=changed),self.assertRaises(ValueError):
                plan.create_smart_constructor(nodes,counter,**{**arguments(),**changed})
        with self.assertRaises(ValueError):
            plan.create_smart(nodes,{**counter,'int_1':2**63-3},name='No overflow',tag_ids=['111'],
                definition_timestamp=DEFINED,**arguments())
