import json
from pathlib import Path
from tests.test_memory_safe_final_validation import run_chunked_validation
root=Path.cwd()
folder=root/'.prompt8-validation-final/repair-execution'
result=run_chunked_validation(root=root,targets=json.loads((folder/'targets.json').read_text()),max_tests=5,timeout_seconds=180,output_dir=folder)
print(json.dumps(result,indent=2))
