#!/usr/bin/env python3
from __future__ import annotations
import csv,importlib.util,json,pickle,sys
from collections import defaultdict
from datetime import date,timedelta
from pathlib import Path
import os
ROOT=Path(os.environ.get('TEMPORAL_INVOICE_ROOT', Path(__file__).resolve().parents[1]))
SPEC=importlib.util.spec_from_file_location('H',ROOT/'reproducibility/benchmark_harmonized.py');H=importlib.util.module_from_spec(SPEC);sys.modules['H']=H;assert SPEC.loader;SPEC.loader.exec_module(H)
B=H.B

def main_uid(uid):return str(uid).startswith('main:')

def pair_path_fragments(in_fr,out_fr):
    i=j=0
    ri=in_fr[0][1] if in_fr else 0; rj=out_fr[0][1] if out_fr else 0
    while i<len(in_fr) and j<len(out_fr):
        q=min(ri,rj);yield in_fr[i][0],out_fr[j][0],q
        ri-=q;rj-=q
        if ri==0:i+=1;ri=in_fr[i][1] if i<len(in_fr) else 0
        if rj==0:j+=1;rj=out_fr[j][1] if j<len(out_fr) else 0

def op_attribution(log):
    # Returns 2x symmetric, 2x conservative, 2x liberal PMR in cents.
    if log['kind']=='cycle':
        a=sum(q for _eid,frs in log['edge_fragments'] for uid,q,*_ in frs if main_uid(uid))
        return 2*a,2*a,2*a
    if log['bilateral']:
        a=sum(q for frs in (log['in_fragments'],log['out_fragments']) for uid,q,*_ in frs if main_uid(uid))
        return 2*a,2*a,2*a
    sym2=cons2=lib2=0
    for u1,u2,q in pair_path_fragments(log['in_fragments'],log['out_fragments']):
        y1=main_uid(u1);y2=main_uid(u2)
        sym2 += q*(int(y1)+int(y2))
        if y1 and y2: cons2 += 2*q
        if y1 or y2: lib2 += 2*q
    return sym2,cons2,lib2

def result_attribution(result,horizon):
    daily_sym2=[0]*horizon;daily_cons2=[0]*horizon;daily_lib2=[0]*horizon
    for log in result['logs']:
        s,c,l=op_attribution(log);d=log['day'];daily_sym2[d]+=s;daily_cons2[d]+=c;daily_lib2[d]+=l
    def cumulative(xs):
        out=[];z=0
        for x in xs:z+=x;out.append(z)
        return out
    cs,cc,cl=map(cumulative,(daily_sym2,daily_cons2,daily_lib2))
    total2=2*result['pmr']
    return {'total_pmr_cents':result['pmr'],'attributed_2023_symmetric_half_cents':cs[-1] if cs else 0,
            'attributed_2023_conservative_half_cents':cc[-1] if cc else 0,
            'attributed_2023_liberal_half_cents':cl[-1] if cl else 0,
            'attributed_2024_symmetric_half_cents':total2-(cs[-1] if cs else 0),
            'curve_symmetric_half_cents':cs,'curve_conservative_half_cents':cc,'curve_liberal_half_cents':cl}

def slim_attr(a):return {k:v for k,v in a.items() if not k.startswith('curve_')}

def run(validated=False,dedup=False):
    start=date(2023,1,1);end=date(2024,2,8);h=(end-start).days+1
    r23,a23,*_=H.load_main_year(2023,end,validated,dedup,False)
    r24,a24=H.load_2024_buffer(start,end,validated,dedup)
    recs=r23+r24;base=B.State(recs,horizon=h)
    suffix=('_validated' if validated else '')+('_dedup' if dedup else '')
    label='carryover_2023_with_2024_buffer'+suffix
    cache=ROOT/'cycle_caches'/f'{label}_cycles.pkl'
    candidates,inventory,enum=pickle.load(open(cache,'rb'))
    cycles=[]
    for length,eids,cap,day in candidates:
        nodes=tuple(base.edges[eids[i]]['u'] for i in range(length));cycles.append((nodes,tuple(eids)))
    po=B.path_offline(base,True);co=H.cycle_offline_screened(base,candidates,True)
    pd=B.path_daily(base,True);cd=B.cycle_daily(base,cycles,True)
    attrs={
      'offline_cycle':result_attribution(co,h),'offline_path':result_attribution(po,h),
      'daily_cycle':result_attribution(cd,h),'daily_path':result_attribution(pd,h)}
    out={'label':label,'horizon_days':h,'mass_2023_cents':sum(r.amount for r in r23),'mass_2024_buffer_cents':sum(r.amount for r in r24),'records_2023':len(r23),'records_2024_buffer':len(r24),'attributes':{k:slim_attr(v) for k,v in attrs.items()}}
    # derived percentages and bounds
    m23=out['mass_2023_cents']
    for regime in ['offline','daily']:
        for method in ['cycle','path']:
            a=out['attributes'][f'{regime}_{method}']
            for typ in ['symmetric','conservative','liberal']:
                a[f'attributed_2023_{typ}_percent']=100*(a[f'attributed_2023_{typ}_half_cents']/2)/m23
        c=out['attributes'][f'{regime}_cycle']['attributed_2023_symmetric_half_cents']/2
        p=out['attributes'][f'{regime}_path']['attributed_2023_symmetric_half_cents']/2
        out[f'{regime}_attributed_advantage_cents']=p-c
        out[f'{regime}_attributed_advantage_pp']=100*(p-c)/m23
    outdir=ROOT/'new_results';outdir.mkdir(exist_ok=True)
    (outdir/f'{label}_attribution.json').write_text(json.dumps(out,indent=2),encoding='utf-8')
    # daily curve
    with (outdir/f'{label}_attributed_daily_curves.csv').open('w',newline='') as f:
        w=csv.writer(f);w.writerow(['day','date','cycle_total_cents','path_total_cents','cycle_2023_symmetric_eur','path_2023_symmetric_eur','path_minus_cycle_2023_eur','path_2023_conservative_eur','path_2023_liberal_eur'])
        for d in range(h):
            ctot=cd['curve'][d];ptot=pd['curve'][d]
            cs=attrs['daily_cycle']['curve_symmetric_half_cents'][d]/200
            ps=attrs['daily_path']['curve_symmetric_half_cents'][d]/200
            pc=attrs['daily_path']['curve_conservative_half_cents'][d]/200
            pl=attrs['daily_path']['curve_liberal_half_cents'][d]/200
            w.writerow([d,(start+timedelta(days=d)).isoformat(),ctot,ptot,cs,ps,ps-cs,pc,pl])
    print(json.dumps(out,indent=2))

if __name__=='__main__':
    run(False,False)
    run(True,False)
    run(False,True)
