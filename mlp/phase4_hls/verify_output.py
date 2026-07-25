"""
Structural verification of the hls4ml-generated C++ project.
Run after convert.py to confirm the project is well-formed before synthesis.
"""

import os


def verify_hls_project_structure(hls_project_dir):
    required = [
        'firmware/myproject.cpp',
        'firmware/myproject.h',
        'firmware/weights',
        'firmware/nnet_utils',
    ]
    print("Verifying HLS project structure...")
    all_present = True
    for f in required:
        path = os.path.join(hls_project_dir, f)
        exists = os.path.exists(path)
        print(f"  {f}: {'OK' if exists else 'MISSING'}")
        if not exists:
            all_present = False
    return all_present


def verify_weight_files(hls_project_dir):
    weights_dir = os.path.join(hls_project_dir, 'firmware/weights')
    if not os.path.isdir(weights_dir):
        print("WARNING: weights/ directory not found")
        return False
    weight_files = os.listdir(weights_dir)
    print(f"\nWeight files generated ({len(weight_files)} total):")
    for f in sorted(weight_files):
        print(f"  {f}")
    if len(weight_files) < 8:
        print(f"WARNING: Expected at least 8 weight files, got {len(weight_files)}")
        return False
    return True


def spot_check_generated_cpp(hls_project_dir):
    cpp_path = os.path.join(hls_project_dir, 'firmware/myproject.cpp')
    if not os.path.isfile(cpp_path):
        print("ERROR: myproject.cpp not found")
        return False
    with open(cpp_path) as f:
        content = f.read()
    checks = {
        'dense_1 layer present':  'dense_1' in content,
        'dense_2 layer present':  'dense_2' in content,
        'dense_3 layer present':  'dense_3' in content,
        'ap_fixed types present': 'ap_fixed' in content,
        'nnet functions present':  'nnet' in content,
        'ReLU activation present': 'relu' in content.lower(),
    }
    print("\nGenerated C++ spot checks:")
    all_pass = True
    for check, result in checks.items():
        print(f"  {check}: {'PASS' if result else 'FAIL'}")
        if not result:
            all_pass = False
    return all_pass


if __name__ == '__main__':
    HLS_DIR = 'mlp/phase4_hls/hls_project'
    ok1 = verify_hls_project_structure(HLS_DIR)
    ok2 = verify_weight_files(HLS_DIR)
    ok3 = spot_check_generated_cpp(HLS_DIR)
    if ok1 and ok2 and ok3:
        print("\nAll structure checks passed.")
    else:
        print("\nSome checks failed — re-run convert.py or inspect the project.")
