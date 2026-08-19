#!/usr/bin/env python3
"""Exact optimized daily executor for large annual graphs.

This module implements the same deterministic daily policies as
benchmark_common_day_base.py. It improves runtime without changing the policy by:
1. visiting only nodes with both incoming and outgoing edges for path clearing;
2. pre-indexing each cycle by the days on which all of its edges have positive
   initial capacity. Residual capacities only decrease, so a cycle inactive
   initially on a day cannot become active on that day.
"""
from __future__ import annotations
import argparse,csv,heapq,importlib.util,json,pickle,sys,time
from collections import defaultdict
from pathlib import Path
from typing import Any,Dict,Sequence,Tuple
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
H_PATH=ROOT/'reproducibility/benchmark_harmonized.py'
spec=importlib.util.spec_from_file_location('harmonized_opt',H_PATH)
H=importlib.util.module_from_spec(spec);sys.modules['harmonized_opt']=H
assert spec.loader;spec.loader.exec_module(H)

def path_daily_optimized(base):
    state=base.copy();start_time=time.time();incoming=defaultdict(list);outgoing=defaultdict(list)
    for edge_id,edge in enumerate(state.edges):
        outgoing[edge['u']].append(edge_id);incoming[edge['v']].append(edge_id)
    intermediaries=sorted(set(incoming).intersection(outgoing))
    operations=pmr=compression=instruction_mass=0;curve=[]
    acceleration_numerator=positive_acceleration_numerator=positive_acceleration_mass=0
    def pop_current(heap,day):
        while heap:
            neg_capacity,_,edge_id=heapq.heappop(heap);current=int(state.edges[edge_id]['cap'][day])
            if current>0 and -neg_capacity==current:return edge_id
        return None
    for day in range(state.horizon):
        for intermediary in intermediaries:
            in_heap=[];out_heap=[]
            for edge_id in incoming[intermediary]:
                capacity=int(state.edges[edge_id]['cap'][day])
                if capacity>0:heapq.heappush(in_heap,(-capacity,state.edges[edge_id]['u'],edge_id))
            if not in_heap:continue
            for edge_id in outgoing[intermediary]:
                capacity=int(state.edges[edge_id]['cap'][day])
                if capacity>0:heapq.heappush(out_heap,(-capacity,state.edges[edge_id]['v'],edge_id))
            if not out_heap:continue
            while True:
                best_in=pop_current(in_heap,day);best_out=pop_current(out_heap,day)
                if best_in is None or best_out is None:break
                amount=min(int(state.edges[best_in]['cap'][day]),int(state.edges[best_out]['cap'][day]))
                payer=state.edges[best_in]['u'];payee=state.edges[best_out]['v']
                in_fragments=state.consume(best_in,day,amount);out_fragments=state.consume(best_out,day,amount)
                operations+=1;compression+=2*amount
                if payer==payee:pmr+=2*amount
                else:
                    pmr+=amount;instruction_mass+=amount;i=j=0;rem_i=in_fragments[0][1];rem_j=out_fragments[0][1]
                    while i<len(in_fragments) and j<len(out_fragments):
                        paired=min(rem_i,rem_j);acceleration=max(0,in_fragments[i][5]-out_fragments[j][5])
                        acceleration_numerator+=paired*acceleration
                        if acceleration>0:
                            positive_acceleration_numerator+=paired*acceleration;positive_acceleration_mass+=paired
                        rem_i-=paired;rem_j-=paired
                        if rem_i==0:i+=1;rem_i=in_fragments[i][1] if i<len(in_fragments) else 0
                        if rem_j==0:j+=1;rem_j=out_fragments[j][1] if j<len(out_fragments) else 0
                new_in=int(state.edges[best_in]['cap'][day]);new_out=int(state.edges[best_out]['cap'][day])
                if new_in>0:heapq.heappush(in_heap,(-new_in,state.edges[best_in]['u'],best_in))
                if new_out>0:heapq.heappush(out_heap,(-new_out,state.edges[best_out]['v'],best_out))
        curve.append(pmr)
    return {'method':'path_daily','operations':operations,'pmr':pmr,'compression':compression,
            'instruction_mass':instruction_mass,'residual_mass':state.residual_mass(),
            'runtime_seconds':time.time()-start_time,'curve':curve,
            'mean_acceleration_days':acceleration_numerator/instruction_mass if instruction_mass else 0.0,
            'positive_only_mean_days':positive_acceleration_numerator/positive_acceleration_mass if positive_acceleration_mass else 0.0,
            'accelerated_mass_share':positive_acceleration_mass/instruction_mass if instruction_mass else 0.0}

def cycle_daily_optimized(base,cycles:Sequence[Tuple[Tuple[int,...],Tuple[int,...]]]):
    state=base.copy();start_time=time.time();horizon=state.horizon
    edge_masks=[]
    for edge in state.edges:
        packed=np.packbits((edge['cap']>0).astype(np.uint8),bitorder='little')
        edge_masks.append(int.from_bytes(packed.tobytes(),'little'))
    candidates_by_day=[[] for _ in range(horizon)]
    for cycle_index,(_,edge_ids) in enumerate(cycles):
        mask=edge_masks[edge_ids[0]]
        for edge_id in edge_ids[1:]:mask&=edge_masks[edge_id]
        while mask:
            least=mask & -mask;day=least.bit_length()-1
            if day<horizon:candidates_by_day[day].append(cycle_index)
            mask-=least
    operations=pmr=0;curve=[]
    for day in range(horizon):
        heap=[]
        for cycle_index in candidates_by_day[day]:
            nodes,edge_ids=cycles[cycle_index]
            amount=min(int(state.edges[edge_id]['cap'][day]) for edge_id in edge_ids)
            if amount<=0:continue
            versions=tuple(state.edges[edge_id]['version'] for edge_id in edge_ids)
            heapq.heappush(heap,(-len(nodes)*amount,len(nodes),nodes,cycle_index,amount,versions))
        while heap:
            _,length,nodes,cycle_index,amount,versions=heapq.heappop(heap);edge_ids=cycles[cycle_index][1]
            current_versions=tuple(state.edges[edge_id]['version'] for edge_id in edge_ids)
            if current_versions!=versions:
                refreshed=min(int(state.edges[edge_id]['cap'][day]) for edge_id in edge_ids)
                if refreshed>0:heapq.heappush(heap,(-length*refreshed,length,nodes,cycle_index,refreshed,current_versions))
                continue
            refreshed=min(int(state.edges[edge_id]['cap'][day]) for edge_id in edge_ids)
            if refreshed!=amount:
                if refreshed>0:heapq.heappush(heap,(-length*refreshed,length,nodes,cycle_index,refreshed,current_versions))
                continue
            for edge_id in edge_ids:state.consume(edge_id,day,amount)
            operations+=1;pmr+=length*amount
        curve.append(pmr)
    return {'method':'cycle_daily','operations':operations,'pmr':pmr,'compression':pmr,
            'instruction_mass':0,'residual_mass':state.residual_mass(),
            'runtime_seconds':time.time()-start_time,'curve':curve}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--year',type=int,required=True);ap.add_argument('--out',type=Path,default=ROOT/'series_results');a=ap.parse_args()
    recs,audit,start,end,h=H.load_main_year(a.year,H.date(a.year,12,31));base=H.B.State(recs,horizon=h)
    cache=ROOT/'cycle_caches'/f'annual_{a.year}_cycles.pkl'
    with cache.open('rb') as f:candidates,inventory,enum_seconds=pickle.load(f)
    cycles=[]
    for length,edge_ids,_,_ in candidates:
        nodes=tuple(base.edges[edge_ids[i]]['u'] for i in range(length));cycles.append((nodes,tuple(edge_ids)))
    path=path_daily_optimized(base);cycle=cycle_daily_optimized(base,cycles)
    summary={'label':f'annual_{a.year}','period_start':start.isoformat(),'period_end':end.isoformat(),'horizon_days':h,
             'load_audit':audit,'initial_mass':base.initial_mass,'topology':H.B.topology_metrics(recs),
             'cycle_inventory':inventory,'cycle_count_total':sum(inventory.values()),'initially_feasible_cycles':len(candidates),
             'cycle_enumeration_and_screen_seconds':enum_seconds,
             'daily':{'cycle':H.slim(cycle),'path':H.slim(path),'advantage':path['pmr']-cycle['pmr'],
                      'advantage_pp':100*(path['pmr']-cycle['pmr'])/base.initial_mass,
                      'auc_advantage':sum(p-c for p,c in zip(path['curve'],cycle['curve'])),
                      'optimized_exact_policy_execution':True}}
    a.out.mkdir(parents=True,exist_ok=True);(a.out/f'annual_{a.year}_summary.json').write_text(json.dumps(summary,indent=2))
    with (a.out/f'annual_{a.year}_daily_curves.csv').open('w',newline='') as f:
        w=csv.writer(f);w.writerow(['day','date','cycle_pmr_cents','path_pmr_cents','advantage_cents'])
        for day,(c,p) in enumerate(zip(cycle['curve'],path['curve'])):
            w.writerow([day,(start+H.timedelta(days=day)).isoformat(),c,p,p-c])
    print(json.dumps({'year':a.year,'path_pmr':path['pmr'],'cycle_pmr':cycle['pmr'],'path_operations':path['operations'],'cycle_operations':cycle['operations']},indent=2))
if __name__=='__main__':main()
