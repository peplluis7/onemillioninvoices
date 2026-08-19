#!/usr/bin/env python3
from __future__ import annotations
import argparse,csv,gc,hashlib,heapq,importlib.util,json,math,os,pickle,sys,time
from collections import Counter,defaultdict
from dataclasses import dataclass
from datetime import date,datetime,timedelta
from pathlib import Path
from typing import Any,Dict,List,Optional,Sequence,Tuple
import networkx as nx

ROOT=Path(os.environ.get('TEMPORAL_INVOICE_ROOT', Path(__file__).resolve().parents[1]))
MAIN_TSV=ROOT/'all_invoices_selected.tsv'
Y2024_TSV=ROOT/'2024_selected.tsv'
BASE_PATH=ROOT/'reproducibility/benchmark_common_day_base.py'

def load_base_module():
    spec=importlib.util.spec_from_file_location('common_day_base_new',BASE_PATH)
    m=importlib.util.module_from_spec(spec);sys.modules['common_day_base_new']=m
    assert spec.loader;spec.loader.exec_module(m);return m
B=load_base_module()

KNOWN_JULY='Historical source overlaps July 2022 coverage'
KNOWN_DEC='December source duplicates November source'

def parse_iso(s:str)->Optional[date]:
    s=(s or '').strip()
    if len(s)>=10:
        try:return date.fromisoformat(s[:10])
        except ValueError:return None
    return None

def cents(s:str)->Optional[int]:
    try:
        x=float((s or '').strip())
        if not math.isfinite(x):return None
        return int(round(x*100))
    except: return None

def stable_hash(parts:Sequence[Any])->str:
    return hashlib.sha256('\x1f'.join(str(x) for x in parts).encode()).hexdigest()

def is_cancelled(s:str)->bool:
    return (s or '').strip().lower() in {'1','y','yes','true'}

def is_validated(source_file:str,supplier_validation:str,buyer_validation:str,invoice_status:str)->bool:
    if 'database' in (source_file or '').lower():
        return supplier_validation.strip().upper()=='Y' and buyer_validation.strip().upper()=='Y'
    return invoice_status.strip().lower()=='validata'

def load_main_year(year:int,horizon_end:date,validated_only:bool=False,deduplicate:bool=False,include_known_source_copies:bool=False,amount_field:str='value'):
    if year==2021: raise ValueError('2021 must use the separately curated twelve-workbook corpus.')
    start=date(year,1,1);h=(horizon_end-start).days+1
    recs=[];audit=Counter();seen=set();dup_counts=Counter();dup_amount={}
    with MAIN_TSV.open('r',encoding='utf-8',newline='') as f:
        rd=csv.DictReader(f,delimiter='\t')
        for row in rd:
            issue=parse_iso(row['issue'])
            if issue is None or issue.year!=year: continue
            audit['raw_selected']+=1
            flag=row.get('quality_flag','')
            if not include_known_source_copies and (KNOWN_JULY in flag or KNOWN_DEC in flag):
                audit['removed_known_source_copy']+=1;continue
            amount=cents(row[amount_field])
            if amount is None or amount<=0:audit['removed_bad_amount']+=1;continue
            due=parse_iso(row['due'])
            if due is None:audit['removed_bad_due']+=1;continue
            if due<issue:audit['removed_negative_maturity']+=1;continue
            if is_cancelled(row['canceled']):audit['removed_cancelled']+=1;continue
            debtor=row['buyer_code'].strip();creditor=row['supplier_code'].strip()
            if not debtor or not creditor:audit['removed_bad_party']+=1;continue
            if debtor==creditor:audit['removed_self']+=1;continue
            if validated_only and not is_validated(row['source_file'],row['supplier_validation'],row['buyer_validation'],row['invoice_status']):
                audit['removed_validation']+=1;continue
            status=(row['supplier_validation'].strip()+'/'+row['buyer_validation'].strip()+'/'+row['invoice_status'].strip())
            fp=stable_hash((debtor,creditor,row['series'].strip().upper(),row['number'].strip(),issue.isoformat(),due.isoformat(),amount,status))
            dup_counts[fp]+=1;dup_amount[fp]=amount
            if deduplicate and fp in seen:audit['removed_duplicate']+=1;continue
            seen.add(fp)
            uid=f"main:{row['row_id']}"
            recs.append(B.AtomicRecord(uid=uid,debtor=f'code:{debtor}',creditor=f'code:{creditor}',amount=amount,
                issue_idx=(issue-start).days,due_idx=min((due-start).days,h-1),issue_ord=issue.toordinal(),due_ord=due.toordinal(),
                status=status,source=row['source_file'],fingerprint=fp))
    dups={k:n for k,n in dup_counts.items() if n>1}
    audit['duplicate_groups']=len(dups);audit['duplicate_excess_rows']=sum(n-1 for n in dups.values());audit['duplicate_excess_amount']=sum((n-1)*dup_amount[k] for k,n in dups.items())
    audit['retained_rows']=len(recs);audit['retained_amount']=sum(r.amount for r in recs)
    return recs,dict(audit),start,horizon_end,h

def load_2024_buffer(start:date,horizon_end:date,validated_only:bool=False,deduplicate:bool=False,amount_field:str='value'):
    h=(horizon_end-start).days+1;recs=[];audit=Counter();seen=set();dup_counts=Counter();dup_amount={}
    with Y2024_TSV.open('r',encoding='utf-8',newline='') as f:
        rd=csv.DictReader(f,delimiter='\t')
        for row in rd:
            audit['raw_rows']+=1
            issue=parse_iso(row['issue']);due=parse_iso(row['due'])
            if issue is None or issue<date(2024,1,1) or issue>horizon_end:audit['removed_outside']+=1;continue
            amount=cents(row[amount_field])
            if amount is None or amount<=0:audit['removed_bad_amount']+=1;continue
            if due is None:audit['removed_bad_due']+=1;continue
            if due<issue:audit['removed_negative_maturity']+=1;continue
            debtor=row['buyer_code'].strip();creditor=row['supplier_code'].strip()
            if not debtor or not creditor:audit['removed_bad_party']+=1;continue
            if debtor==creditor:audit['removed_self']+=1;continue
            if validated_only and row['invoice_status'].strip().lower()!='validata':audit['removed_validation']+=1;continue
            status=row['invoice_status'].strip();fp=stable_hash((debtor,creditor,row['series'].strip().upper(),row['number'].strip(),issue.isoformat(),due.isoformat(),amount,status))
            dup_counts[fp]+=1;dup_amount[fp]=amount
            if deduplicate and fp in seen:audit['removed_duplicate']+=1;continue
            seen.add(fp)
            recs.append(B.AtomicRecord(uid=f"buffer24:{row['row_id']}",debtor=f'code:{debtor}',creditor=f'code:{creditor}',amount=amount,
                issue_idx=(issue-start).days,due_idx=min((due-start).days,h-1),issue_ord=issue.toordinal(),due_ord=due.toordinal(),status=status,source=row['source_file'],fingerprint=fp))
    dups={k:n for k,n in dup_counts.items() if n>1}
    audit['duplicate_groups']=len(dups);audit['duplicate_excess_rows']=sum(n-1 for n in dups.values());audit['duplicate_excess_amount']=sum((n-1)*dup_amount[k] for k,n in dups.items())
    audit['retained_rows']=len(recs);audit['retained_amount']=sum(r.amount for r in recs)
    return recs,dict(audit)

def canonical_cycle(nodes:List[int])->Tuple[int,...]:
    n=len(nodes);r=min(range(n),key=lambda i:tuple(nodes[i:]+nodes[:i]));return tuple(nodes[r:]+nodes[:r])

def enumerate_and_screen_cycles(base,length_bound=8,progress=False):
    g=nx.DiGraph();g.add_edges_from((e['u'],e['v']) for e in base.edges)
    inventory=Counter();feasible=[];t0=time.time();count=0
    for raw in nx.simple_cycles(g,length_bound=length_bound):
        nodes=canonical_cycle(raw);n=len(nodes);inventory[n]+=1;count+=1
        eids=tuple(base.edge_map[(nodes[i],nodes[(i+1)%n])] for i in range(n));cap,day=base.cycle_common_day_capacity(eids)
        if cap>0:feasible.append((n,nodes,eids,cap,day))
        if progress and count%100000==0:print('cycles',count,'feasible',len(feasible),'sec',time.time()-t0,flush=True)
    feasible.sort(key=lambda x:(x[0],x[1]))
    return [(x[0],x[2],x[3],x[4]) for x in feasible],dict(sorted(inventory.items())),time.time()-t0

def cycle_offline_screened(base,candidates,keep_logs=True):
    s=base.copy();t0=time.time();heap=[]
    for rank,(length,eids,cap,day) in enumerate(candidates):
        versions=tuple(s.edges[e]['version'] for e in eids)
        heapq.heappush(heap,(-length*cap,length,rank,day,cap,versions))
    ops=pmr=reev=0;logs=[]
    while heap:
        _,length,rank,day,cap,versions=heapq.heappop(heap);eids=candidates[rank][1];cur=tuple(s.edges[e]['version'] for e in eids)
        if cur!=versions:
            c2,d2=s.cycle_common_day_capacity(eids);reev+=1
            if c2>0:heapq.heappush(heap,(-length*c2,length,rank,d2,c2,cur))
            continue
        c2,d2=s.cycle_common_day_capacity(eids)
        if c2!=cap or d2!=day:
            reev+=1
            if c2>0:heapq.heappush(heap,(-length*c2,length,rank,d2,c2,cur))
            continue
        fr=[]
        for eid in eids:fr.append((eid,s.consume(eid,day,cap)))
        ops+=1;pmr+=length*cap
        if keep_logs:logs.append({'kind':'cycle','day':day,'amount':cap,'edges':eids,'edge_fragments':fr})
    return {'method':'cycle_offline','operations':ops,'pmr':pmr,'compression':pmr,'instruction_mass':0,'residual_mass':s.residual_mass(),'runtime_seconds':time.time()-t0,'reevaluations':reev,'logs':logs}

def slim(r):return {k:v for k,v in r.items() if k not in {'logs','curve'}}

def run_period(label,recs,audit,start,end,h,outdir,run_offline=True,run_daily=True,cycle_cache:Optional[Path]=None):
    outdir.mkdir(parents=True,exist_ok=True);base=B.State(recs,horizon=h);top=B.topology_metrics(recs)
    print(label,'records',len(recs),'mass bn',base.initial_mass/1e11,'firms',top['firms'],'edges',top['edges'],flush=True)
    if cycle_cache and cycle_cache.exists():
        with cycle_cache.open('rb') as f:candidates,inventory,enum_seconds=pickle.load(f)
        print('loaded cycles',sum(inventory.values()),'feasible',len(candidates),flush=True)
    else:
        candidates,inventory,enum_seconds=enumerate_and_screen_cycles(base,8,progress=True)
        if cycle_cache:
            with cycle_cache.open('wb') as f:pickle.dump((candidates,inventory,enum_seconds),f,protocol=pickle.HIGHEST_PROTOCOL)
    summary={'label':label,'period_start':start.isoformat(),'period_end':end.isoformat(),'horizon_days':h,'load_audit':audit,'initial_mass':base.initial_mass,'topology':top,'cycle_inventory':inventory,'cycle_count_total':sum(inventory.values()),'initially_feasible_cycles':len(candidates),'cycle_enumeration_and_screen_seconds':enum_seconds}
    if run_offline:
        po=B.path_offline(base,keep_logs=True);vo=B.replay_validate(recs,base,po);print(label,'path offline',po['pmr']/1e11,vo['all_pass'],flush=True)
        co=cycle_offline_screened(base,candidates,True);vco=B.replay_validate(recs,base,co);print(label,'cycle offline',co['pmr']/1e11,vco['all_pass'],flush=True)
        summary['offline']={'cycle':slim(co),'path':slim(po),'advantage':po['pmr']-co['pmr'],'advantage_pp':100*(po['pmr']-co['pmr'])/base.initial_mass,'validation_cycle':vco,'validation_path':vo}
    if run_daily:
        pd=B.path_daily(base,keep_logs=True);vd=B.replay_validate(recs,base,pd);print(label,'path daily',pd['pmr']/1e11,vd['all_pass'],flush=True)
        cyc_simple=[(tuple(),tuple(c[1])) for c in candidates]
        # cycle_daily uses node tuple for tie-breaking; reconstruct nodes from edges.
        cyc_simple=[]
        for length,eids,cap,day in candidates:
            nodes=tuple(base.edges[eids[i]]['u'] for i in range(length))
            cyc_simple.append((nodes,tuple(eids)))
        cd=B.cycle_daily(base,cyc_simple,keep_logs=True);vcd=B.replay_validate(recs,base,cd);print(label,'cycle daily',cd['pmr']/1e11,vcd['all_pass'],flush=True)
        summary['daily']={'cycle':slim(cd),'path':slim(pd),'advantage':pd['pmr']-cd['pmr'],'advantage_pp':100*(pd['pmr']-cd['pmr'])/base.initial_mass,'auc_advantage':sum(p-c for p,c in zip(pd['curve'],cd['curve'])),'validation_cycle':vcd,'validation_path':vd}
        with (outdir/f'{label}_daily_curves.csv').open('w',newline='') as f:
            w=csv.writer(f);w.writerow(['day','date','cycle_pmr_cents','path_pmr_cents','advantage_cents'])
            for d,(c,p) in enumerate(zip(cd['curve'],pd['curve'])):w.writerow([d,(start+timedelta(days=d)).isoformat(),c,p,p-c])
    (outdir/f'{label}_summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    return summary

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--period',choices=['2022','2023','year'],required=True);ap.add_argument('--year',type=int);ap.add_argument('--validated-only',action='store_true');ap.add_argument('--deduplicate',action='store_true');ap.add_argument('--include-2024-buffer',action='store_true');ap.add_argument('--include-known-source-copies',action='store_true');ap.add_argument('--offline-only',action='store_true');ap.add_argument('--daily-only',action='store_true');ap.add_argument('--out',type=Path,default=ROOT/'new_results');a=ap.parse_args()
    if a.period=='2022':year=2022;end=date(2022,12,31);label='harmonized_2022'
    elif a.period=='2023':year=2023;end=date(2024,2,8);label='carryover_2023'
    else:
        if not a.year:
            raise SystemExit('--year required')
        year=a.year
        end=date(year,12,31)
        label=f'annual_{year}'
    recs,audit,start,end,h=load_main_year(year,end,a.validated_only,a.deduplicate,a.include_known_source_copies)
    if a.period=='2023' and a.include_2024_buffer:
        b24,a24=load_2024_buffer(start,end,a.validated_only,a.deduplicate);recs.extend(b24);audit={'main':audit,'buffer_2024':a24,'combined_records':len(recs),'combined_amount':sum(r.amount for r in recs)};label+='_with_2024_buffer'
    suffix=('_validated' if a.validated_only else '')+('_dedup' if a.deduplicate else '')+('_ascopies' if a.include_known_source_copies else '')
    label+=suffix
    cache=ROOT/'cycle_caches'/f'{label}_cycles.pkl';cache.parent.mkdir(exist_ok=True)
    run_period(label,recs,audit,start,end,h,a.out,not a.daily_only,not a.offline_only,cache)
if __name__=='__main__':main()
