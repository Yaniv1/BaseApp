from test.tests.base.output import test_dataconverter_error_handling, test_dataconverter_json_column

for fn in [test_dataconverter_error_handling, test_dataconverter_json_column]:
    r = fn()
    print(f"=== {fn.__name__} => {r['status']} ===")
    for c in r["criteria"]:
        print(f"  [{c['status']}] {c['name']}")
    errs = (r.get("data") or {}).get("errors")
    if errs:
        print(f"  errors: {errs}")
