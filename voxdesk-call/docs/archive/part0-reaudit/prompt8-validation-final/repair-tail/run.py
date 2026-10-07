import json
from collections import defaultdict
from pathlib import Path
from tests.test_memory_safe_final_validation import run_chunked_validation
root=Path.cwd()
base=root/'.prompt8-validation-final'
targets=json.loads((base/'repair-execution/targets.json').read_text())[455:]
groups=defaultdict(list)
for node in targets:
    groups[node.split('::')[0]].append(node)
for index,(module,nodes) in enumerate(groups.items(),1):
    folder=base/'repair-tail'/f'lane-{index:02}'
    result=run_chunked_validation(root=root,targets=nodes,max_tests=5,timeout_seconds=180,output_dir=folder)
    print(module,result['passed'],result['failed'],result['skipped'],result['unconfirmed'],flush=True)
