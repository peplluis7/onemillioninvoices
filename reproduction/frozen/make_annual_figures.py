#!/usr/bin/env python3
from pathlib import Path
import csv
import matplotlib.pyplot as plt

ROOT=Path(__file__).resolve().parents[1]
rows=list(csv.DictReader((ROOT/'results/annual_series_2012_2023.csv').open()))
years=[int(r['year']) for r in rows]

def f(key):return [float(r[key]) for r in rows]

def save(name):
    plt.tight_layout();plt.savefig(ROOT/'figures'/name,bbox_inches='tight');plt.close()

plt.figure(figsize=(7.2,4.3))
plt.bar(years,f('mass_bn'))
plt.yscale('log')
plt.xticks(years,rotation=45)
plt.xlabel('Issue year')
plt.ylabel('Retained invoice mass (EUR bn, log scale)')
plt.title('Annual analytical-series scale')
plt.grid(axis='y',alpha=0.25)
save('annual_invoice_mass.pdf')

plt.figure(figsize=(7.2,4.3))
plt.plot(years,f('offline_cycle_pct'),marker='o',label='Cycle netting, L=8')
plt.plot(years,f('offline_path_pct'),marker='o',label='Path-enabled clearing')
plt.xticks(years,rotation=45)
plt.xlabel('Issue year')
plt.ylabel('Offline payable-mass reduction (%)')
plt.title('Annual offline common-day-capacity results')
plt.legend()
plt.grid(alpha=0.25)
save('annual_offline_pmr.pdf')

plt.figure(figsize=(7.2,4.3))
plt.plot(years,f('daily_cycle_pct'),marker='o',label='Cycle netting, L=8')
plt.plot(years,f('daily_path_pct'),marker='o',label='Path-enabled clearing')
plt.xticks(years,rotation=45)
plt.xlabel('Issue year')
plt.ylabel('Daily online payable-mass reduction (%)')
plt.title('Annual daily online results')
plt.legend()
plt.grid(alpha=0.25)
save('annual_daily_pmr.pdf')

plt.figure(figsize=(7.2,4.3))
plt.axhline(0,linewidth=0.8)
plt.plot(years,f('offline_adv_pp'),marker='o',label='Offline advantage')
plt.plot(years,f('daily_adv_pp'),marker='o',label='Daily advantage')
plt.xticks(years,rotation=45)
plt.xlabel('Issue year')
plt.ylabel('Path minus cycle (percentage points)')
plt.title('Annual path-enabled advantage')
plt.legend()
plt.grid(alpha=0.25)
save('annual_advantage_pp.pdf')

plt.figure(figsize=(7.2,4.3))
plt.plot(years,[100*x for x in f('reciprocity')],marker='o',label='Reciprocity')
plt.plot(years,[100*x for x in f('cycle_closure_ratio')],marker='o',label='Local paths in cyclic core')
plt.xticks(years,rotation=45)
plt.xlabel('Issue year')
plt.ylabel('Share (%)')
plt.title('Annual cycle saturation; 2022 is the most reciprocal year')
plt.legend()
plt.grid(alpha=0.25)
save('annual_cycle_saturation.pdf')
