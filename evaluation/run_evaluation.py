"""Read-only UniStats evaluation, adapted from the supervisor's two Colab examples.

Usage: python run_evaluation.py --stats --benchmark
Times include HTTP response transfer and JSON conversion, as in the example.
No application cache, fallback, automatic retries, or database writes are used.
"""
import argparse
import csv
import hashlib
import json
import os
import platform
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    from SPARQLWrapper import SPARQLWrapper, JSON
except ImportError:
    sys.path.insert(0,str(Path(os.environ.get('TEMP','/tmp'))/'unistats-evaluation-tools'))
    from SPARQLWrapper import SPARQLWrapper, JSON

HERE=Path(__file__).resolve().parent
GRAPHS=['http://www.ics.forth.gr/isl/uniStats','http://www.ics.forth.gr/isl/uniStats/personnel','http://www.ics.forth.gr/isl/uniStats_departments']
TRIPLES='''{ SELECT DISTINCT ?s ?p ?o WHERE {
VALUES ?g { '''+' '.join('<'+g+'>' for g in GRAPHS)+''' }
GRAPH ?g { ?s ?p ?o }
} }'''
PREFIXES='PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>\nPREFIX qb: <http://purl.org/linked-data/cube#>\n'
STATS={
 'Total triples':f'SELECT (COUNT(*) AS ?count) WHERE {{ {TRIPLES} }}',
 'Distinct subjects':f'SELECT (COUNT(DISTINCT ?s) AS ?count) WHERE {{ {TRIPLES} }}',
 'Distinct properties':f'SELECT (COUNT(DISTINCT ?p) AS ?count) WHERE {{ {TRIPLES} }}',
 'Distinct objects':f'SELECT (COUNT(DISTINCT ?o) AS ?count) WHERE {{ {TRIPLES} }}',
 'Distinct classes':PREFIXES+f'SELECT (COUNT(DISTINCT ?class) AS ?count) WHERE {{ {TRIPLES} FILTER(?p=rdf:type) BIND(?o AS ?class) }}',
 'URI subjects':f'SELECT (COUNT(DISTINCT ?s) AS ?count) WHERE {{ {TRIPLES} FILTER(isIRI(?s)) }}',
 'URI objects':f'SELECT (COUNT(DISTINCT ?o) AS ?count) WHERE {{ {TRIPLES} FILTER(isIRI(?o)) }}',
 'Distinct literals':f'SELECT (COUNT(DISTINCT ?o) AS ?count) WHERE {{ {TRIPLES} FILTER(isLiteral(?o)) }}',
 'Distinct blank nodes':f'SELECT (COUNT(DISTINCT ?term) AS ?count) WHERE {{ {TRIPLES} {{ BIND(?s AS ?term) }} UNION {{ BIND(?o AS ?term) }} FILTER(isBlank(?term)) }}',
 'Data Cube observations':PREFIXES+f'SELECT (COUNT(DISTINCT ?s) AS ?count) WHERE {{ {TRIPLES} FILTER(?p=rdf:type && ?o=qb:Observation) }}',
 'Data Cube datasets':PREFIXES+f'SELECT (COUNT(DISTINCT ?o) AS ?count) WHERE {{ {TRIPLES} FILTER(?p=qb:dataSet) }}',
}

def save(path,data):path.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')

def execute(endpoint,query,timeout):
    sparql=SPARQLWrapper(endpoint);sparql.setReturnFormat(JSON);sparql.setTimeout(timeout);sparql.setQuery(query)
    start=time.perf_counter()
    response=sparql.query()
    headers=response.response.headers
    if headers.get('X-SQL-State','00000')!='00000':raise RuntimeError('Endpoint reported incomplete execution')
    data=response.convert()
    elapsed=time.perf_counter()-start
    bindings=data['results']['bindings']
    cap=headers.get('X-SPARQL-MaxRows')
    if cap and len(bindings)>=int(cap):raise RuntimeError('Possible endpoint row truncation; result not accepted')
    digest=hashlib.sha256(json.dumps(sorted(json.dumps(r,sort_keys=True) for r in bindings)).encode()).hexdigest()
    return elapsed,data,digest

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--endpoint',default=os.getenv('UNISTATS_ENDPOINT','http://93.115.20.167:8890/sparql'))
    parser.add_argument('--stats',action='store_true');parser.add_argument('--benchmark',action='store_true')
    parser.add_argument('--runs',type=int,default=5);parser.add_argument('--timeout',type=int,default=300)
    parser.add_argument('--output',type=Path,default=HERE/'results')
    args=parser.parse_args()
    if not(args.stats or args.benchmark):args.stats=args.benchmark=True
    if args.runs<2:parser.error('At least two runs are required for sample standard deviation')
    out=args.output;out.mkdir(parents=True,exist_ok=True)
    manifest={'startedAt':datetime.now(timezone.utc).isoformat(),'endpoint':args.endpoint,'graphs':GRAPHS,
              'python':platform.python_version(),'platform':platform.system(),'runs':args.runs,'timeoutSeconds':args.timeout,
              'timing':'HTTP request through JSON conversion; sequential requests; no application cache or explicit warmup; server caches not cleared',
              'statisticsReference':'https://colab.research.google.com/drive/1k_PScEOvqDdrojLxMCgwOtfTR6nLpnW4',
              'benchmarkReference':'https://colab.research.google.com/drive/1-f6CQkm--rboQ8849_yiGhtXt-A8Dlo1'}
    save(out/'environment.json',manifest)
    if args.stats:
        records=[];(HERE/'statistics_queries').mkdir(exist_ok=True)
        for i,(label,query) in enumerate(STATS.items(),1):
            (HERE/'statistics_queries'/f'S{i:02}.rq').write_text(query+'\n',encoding='utf-8')
            row={'metric':label,'query':f'S{i:02}.rq'}
            try:
                elapsed,data,_=execute(args.endpoint,query,args.timeout)
                row.update(value=int(data['results']['bindings'][0]['count']['value']),elapsedSeconds=elapsed)
            except Exception as error:row.update(errorType=type(error).__name__,error=str(error)[:200])
            records.append(row);save(out/'database_statistics.json',records);print(json.dumps(row),flush=True)
    if args.benchmark:
        queries=json.loads((HERE/'query_manifest.json').read_text(encoding='utf-8'))
        raw=[];summary=[]
        for item in queries:
            query=(HERE/'queries'/f"{item['id']}.rq").read_text(encoding='utf-8')
            records=[]
            for run in range(1,args.runs+1):
                row={'query':item['id'],'run':run,'startedAt':datetime.now(timezone.utc).isoformat()}
                try:
                    elapsed,data,digest=execute(args.endpoint,query,args.timeout)
                    row.update(seconds=elapsed,results=len(data['results']['bindings']),resultHash=digest)
                    save(out/f"{item['id']}_results.json",data)
                except Exception as error:row.update(errorType=type(error).__name__,error=str(error)[:200])
                records.append(row);raw.append(row);save(out/'raw_runs.json',raw)
                print(json.dumps(row),flush=True)
            good=[r for r in records if 'seconds' in r]
            complete=len(good)==args.runs and len({r['resultHash'] for r in good})==1
            result={'query':item['id'],'successfulRuns':len(good),'completeAndStable':complete}
            if complete:
                times=[r['seconds'] for r in good]
                result.update(mean=statistics.mean(times),median=statistics.median(times),stdev=statistics.stdev(times),minimum=min(times),maximum=max(times),results=good[0]['results'])
            summary.append(result);save(out/'execution_metrics.json',summary)
        with (out/'execution_metrics.csv').open('w',newline='',encoding='utf-8-sig') as f:
            writer=csv.DictWriter(f,fieldnames=['query','successfulRuns','completeAndStable','mean','median','stdev','minimum','maximum','results']);writer.writeheader();writer.writerows(summary)
    manifest['finishedAt']=datetime.now(timezone.utc).isoformat();save(out/'environment.json',manifest)

if __name__=='__main__':main()
