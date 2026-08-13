import copy, hashlib, json, pathlib, re, unittest
from tests.w2_physical_staging.endpoint_inventory import (
    InventoryError, load, validate_receipt_fixture)

ROOT=pathlib.Path(__file__).resolve().parents[2]
MP=ROOT/'rtl/technology/physical_staging/physical_staging_manifest.json'
def pairs(values):
    out={}
    for k,v in values:
        if k in out: raise ValueError('duplicate key '+k)
        out[k]=v
    return out
def sha(p): return hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()

class ManifestTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.m=json.loads(MP.read_text(),object_pairs_hook=pairs)
    def test_schema_status_order(self):
        self.assertEqual(set(self.m),{'schema','status','repository_commit','goal_order','common_ports','technology_authorities','constraint_templates','designs','source_hashes','test_policy','consumer_contract'})
        self.assertEqual(self.m['schema'],'k2_w2_tech_staged_compositions_v1')
        self.assertIn(self.m['status'],{'HOLD_PENDING_FRESH_GENERIC_GS_LOCKSTEP_AND_RUNNER','READY_FOR_GENUS_AND_INNOVUS'})
        if self.m['status']=='READY_FOR_GENUS_AND_INNOVUS':
            self.assertRegex(self.m['repository_commit'],r'^[0-9a-f]{40}$')
        self.assertEqual(self.m['goal_order'],['fovea_a7','a2_p6','a3_p6'])
        self.assertEqual(list(self.m['designs']),self.m['goal_order'])
    def test_literal_common_ports_and_alias_rejection(self):
        cp=self.m['common_ports']
        self.assertEqual([(x['direction'],x['name'],x.get('width')) for x in cp],
          [('input','ref_clk_i',1),('input','sample_clk_i',1),('input','rst_n',1),
           ('input','source_pending_i',16),('output','source_accept_o',16),
           ('output','link_clk_o',1),('output','link_data_o',None),
           ('output','retire_valid_o',2),('output','retire_addr0_o',4),
           ('output','retire_addr1_o',4),('output','drain_idle_o',1),
           ('output','protocol_error_o',1)])
        self.assertEqual(cp[6]['width_by_design'],{'fovea_a7':2,'a2_p6':5,'a3_p6':5})
        required={x['name'] for x in cp}
        forbidden=self.m['consumer_contract']['forbidden_port_aliases']
        for key,d in self.m['designs'].items():
            text=(ROOT/'rtl/technology/physical_staging'/({'fovea_a7':'w2_fovea_r1_physical_staging_top.sv','a2_p6':'w2_a2_p6_physical_staging_top.sv','a3_p6':'w2_a3_p6_physical_staging_top.sv'}[key])).read_text()
            header=text.split(');',1)[0]
            for p in required:self.assertRegex(header,rf'\b{p}\b')
            for p in forbidden:self.assertNotRegex(header,rf'\b{p}\b')
            self.assertEqual(d['port_signature'][0:4],['ref_clk_i','sample_clk_i','rst_n','source_pending_i[15:0]'])
    def test_authority_and_cell_contract(self):
        ta=self.m['technology_authorities']; self.assertTrue(ta['live_gsclib045']['dffnsrx1_cell_and_interface_verified'])
        self.assertFalse(ta['live_gsclib045']['liberty_timing_arcs_claimed_by_manifest'])
        self.assertEqual(set(ta['cells']),{'TLATNTSCAX2','MX2X1','DFFRHQX1','DFFNSRX1'})
        f=self.m['designs']['fovea_a7']['endpoint_leaf_contract']
        self.assertEqual(set(f),{'path_segment','leaf_counts','preserved_name_prefixes'})
        self.assertEqual(f['leaf_counts'],{'TLATNTSCAX2':1,'MX2X1':2,'DFFRHQX1':2,'DFFNSRX1':5})
        self.assertEqual(f['path_segment'],'w2_endpoint_link__r1')
        for k in ('a2_p6','a3_p6'):
            c=self.m['designs'][k]['endpoint_leaf_contract']
            self.assertEqual(c['leaf_counts'],{'TLATNTSCAX2':1,'MX2X1':5,'DFFRHQX1':5,'DFFNSRX1':12})
            self.assertEqual(c['path_segment'],'w2_endpoint_link__p6')
        for d in self.m['designs'].values():
            self.assertFalse('counts' in d['whole_top_observed_totals'])
            self.assertEqual(d['whole_top_observed_totals']['records'],[])
    def test_source_hashes(self):
        for p,h in self.m['source_hashes'].items(): self.assertEqual(sha(ROOT/p),h,p)
    def test_filelists_are_separated_and_closed(self):
        for key,d in self.m['designs'].items():
            for profile,p in d['filelists'].items():
                lines=[x.strip() for x in (ROOT/p).read_text().splitlines() if x.strip()]
                self.assertEqual(lines[0],'+incdir+rtl/technology/p6')
                self.assertEqual(lines[1],'+define+W2_P6_TECH_'+('GENERIC' if profile=='generic' else 'GSCLIB045'))
                self.assertFalse(any('test' in x or 'gsclib045_test_models' in x for x in lines[2:]))
            if key=='a2_p6':self.assertFalse(any('fovea' in x or 'a3_' in x for x in lines[2:]))
    def test_stable_endpoint_names_and_attributes(self):
        texts='\n'.join((ROOT/p).read_text() for p in ['rtl/technology/p6/w2_p6_clock_boundary.sv','rtl/technology/p6/w2_p6_mux2.sv','rtl/technology/p6/w2_p6_posedge_capture.sv','rtl/technology/p6/w2_p6_negedge_capture.sv','rtl/technology/r1/w2_r1_clock_boundary.sv','rtl/technology/r1/w2_r1_mux2.sv'])
        for token in ('w2_ep_icg_','w2_ep_mux_','w2_ep_pos_','w2_ep_neg_','w2_endpoint_leaf_role','dont_touch'):self.assertIn(token,texts)
        roots=(ROOT/'rtl/technology/physical_staging/w2_p6_atomic_bundle_adapter_tech.sv').read_text()+(ROOT/'rtl/technology/physical_staging/w2_fovea_r1_physical_staging_top.sv').read_text()
        for token in ('w2_endpoint_link__p6','w2_endpoint_link__r1','w2_endpoint_root','keep_hierarchy'):self.assertIn(token,roots)
    def test_extra_cells_pass_wrong_endpoint_bindings_fail(self):
        base=load(ROOT/'tests/w2_physical_staging/fixtures/endpoint_inventory_extra_dffrh.json')
        self.assertEqual(validate_receipt_fixture(base)['whole_top_counts']['DFFRHQX1'],6)
        for name in ('endpoint_inventory_wrong_clock.json','endpoint_inventory_wrong_path.json'):
            mut=load(ROOT/'tests/w2_physical_staging/fixtures'/name); value=copy.deepcopy(base)
            leaf=next(x for x in value['endpoint_leaves'] if x['name']==mut['mutation']['leaf'])
            if mut['mutation']['field']=='name': leaf['name']=mut['mutation']['to']
            else: leaf['connectivity'][mut['mutation']['field'].split('.')[1]]=mut['mutation']['to']
            with self.assertRaisesRegex(InventoryError,mut['expected_error']):validate_receipt_fixture(value)
    def test_pre_nba_monitors(self):
        for p in ('fovea_owner_vs_staged_tb.sv','a2_owner_vs_staged_tb.sv','a3_owner_vs_staged_tb.sv'):
            t=(ROOT/'tests/w2_physical_staging'/p).read_text()
            block=t[t.index('always @(posedge ref_clk)'):]
            self.assertLess(block.index('accepted_edge'),block.index('#1'))
            self.assertNotRegex(t,r'always @\(negedge ref_clk\).*accepted_edge')
            self.assertIn('epoch_',t);self.assertIn('PROTOCOL',t)

    def test_shared_consumer_contract(self):
        c=self.m['consumer_contract']
        self.assertEqual(c['consumers'],['genus','innovus'])
        self.assertEqual(c['required_schema'],self.m['schema'])
        self.assertEqual(c['required_status'],'READY_FOR_GENUS_AND_INNOVUS')
        self.assertTrue(c['require_literal_common_port_signature'])
        self.assertTrue(c['require_endpoint_path_and_leaf_provenance'])

if __name__=='__main__':unittest.main()
