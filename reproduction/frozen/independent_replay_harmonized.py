#!/usr/bin/env python3
from __future__ import annotations
import csv,gzip,json,sys
from collections import defaultdict
from pathlib import Path

def load_records(path):
    rec={};initial_net=defaultdict(int);initial_mass=0
    with gzip.open(path,'rt',encoding='utf-8',newline='') as f:
      for row in csv.DictReader(f):
        uid=row['uid'];amount=int(row['amount_cents']);r={'debtor':row['debtor'],'creditor':row['creditor'],'amount':amount,'issue':int(row['issue_idx']),'due':int(row['due_idx']),'cohort':row['cohort']}
        rec[uid]=r;initial_mass+=amount;initial_net[r['creditor']]+=amount;initial_net[r['debtor']]-=amount
    return rec,initial_mass,initial_net

def validate(records_path,logs_path,reported):
    rec,initial_mass,initial_net=load_records(records_path);used=defaultdict(int);instructions=defaultdict(int);errors=defaultdict(int);op_count=frag_count=0;consumed_total=0;reported_pmr=int(reported['pmr']);reported_residual=int(reported['residual_mass']);reported_instr=int(reported['instruction_mass'])
    with gzip.open(logs_path,'rt',encoding='utf-8') as f:
      for line in f:
        op=json.loads(line);op_count+=1;day=int(op['day']);amt=int(op['amount'])
        if op['kind']=='path':
          sums=[];edges=[]
          for key in ('in_fragments','out_fragments'):
            z=0;edge=None
            for uid,q in op[key]:
              q=int(q);frag_count+=1;z+=q;consumed_total+=q
              if uid not in rec:errors['missing_uid']+=1;continue
              r=rec[uid];used[uid]+=q
              if not (r['issue']<=day<=r['due']):errors['temporal']+=1
              e=(r['debtor'],r['creditor']);edge=e if edge is None else edge
              if edge!=e:errors['mixed_edge']+=1
            sums.append(z);edges.append(edge)
          if sums!=[amt,amt]:errors['bad_sum']+=1
          if edges[0] is not None and edges[1] is not None:
            if edges[0][1]!=edges[1][0]:errors['not_path']+=1
            if bool(op['bilateral'])!=(edges[0][0]==edges[1][1]):errors['bilateral_flag']+=1
            if not op['bilateral']:instructions[(edges[0][0],edges[1][1])]+=amt
        else:
          e_list=[]
          for eid,frs in op['edge_fragments']:
            z=0;edge=None
            for uid,q in frs:
              q=int(q);frag_count+=1;z+=q;consumed_total+=q
              if uid not in rec:errors['missing_uid']+=1;continue
              r=rec[uid];used[uid]+=q
              if not(r['issue']<=day<=r['due']):errors['temporal']+=1
              e=(r['debtor'],r['creditor']);edge=e if edge is None else edge
              if edge!=e:errors['mixed_edge']+=1
            if z!=amt:errors['bad_sum']+=1
            if edge:e_list.append(edge)
          if e_list:
            for i,e in enumerate(e_list):
              if e[1]!=e_list[(i+1)%len(e_list)][0]:errors['not_cycle']+=1
    for uid,q in used.items():
      if q>rec[uid]['amount']:errors['overconsumed']+=1
    residual_mass=sum(r['amount']-used[uid] for uid,r in rec.items())
    final_net=defaultdict(int)
    for uid,r in rec.items():
      q=r['amount']-used[uid]
      if q<0:errors['negative_residual']+=1
      final_net[r['creditor']]+=q;final_net[r['debtor']]-=q
    instruction_mass=0
    for (u,v),q in instructions.items():
      instruction_mass+=q;final_net[v]+=q;final_net[u]-=q
    max_net=max([abs(final_net[k]-initial_net[k]) for k in set(final_net)|set(initial_net)] or [0])
    if max_net:errors['net_position']+=1
    pmr=initial_mass-(residual_mass+instruction_mass)
    if pmr!=reported_pmr:errors['pmr_report']+=1
    if residual_mass!=reported_residual:errors['residual_report']+=1
    if instruction_mass!=reported_instr:errors['instruction_report']+=1
    result={'operations':op_count,'fragments':frag_count,'initial_mass':initial_mass,'consumed_invoice_mass':consumed_total,'residual_mass':residual_mass,'instruction_mass':instruction_mass,'reconstructed_pmr':pmr,'reported_pmr':reported_pmr,'max_net_error_cents':max_net,'errors':dict(errors),'all_pass':not any(errors.values())}
    return result

def main(root):
    root=Path(root);summary=json.loads((root/'executor_summary.json').read_text());results={}
    for name,reported in summary['methods'].items():results[name]=validate(root/'atomic_records.csv.gz',root/f'{name}_operations.jsonl.gz',reported)
    out={'label':summary['label'],'results':results,'all_pass':all(x['all_pass'] for x in results.values())}
    (root/'independent_replay.json').write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out,indent=2))
if __name__=='__main__':main(sys.argv[1])
