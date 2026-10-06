"""Read only the public DNSE specification; never load app/account secrets."""
import re
import requests

URL = "https://cdn.entrade.com.vn/dnse-openapi/doc/dnse-openapi-2026-05-07.yaml"
response = requests.get(URL, timeout=20)
response.raise_for_status()
response.encoding = "utf-8"
lines = response.text.splitlines()
for path in ("/accounts/orders", "/accounts/{accountNo}/orders/{orderId}"):
    matches = [i for i, line in enumerate(lines) if line.strip().strip("'\"") == path + ":"]
    if not matches:
        print("PATH NOT FOUND", path)
        continue
    start = matches[0]
    indent = len(lines[start]) - len(lines[start].lstrip())
    end = next((i for i in range(start + 1, len(lines)) if lines[i].strip() and len(lines[i]) - len(lines[i].lstrip()) <= indent), len(lines))
    section = lines[start:end]
    print("PUBLIC SPEC PATH", path, "LINES", start + 1, end)
    print("remark occurrences", sum("remark" in line.lower() for line in section))
    for i, line in enumerate(section):
        if re.search(r"loanPackageId:|PendingCancel|feeRate:|averagePrice:", line) and i > 0:
            print("SPEC LINE", start + i + 1)
            print("\n".join(section[max(0, i - 1):min(len(section), i + 5)]))
print("Matching order path names:")
print("\n".join(line for line in lines if re.match(r"^  .*accounts.*orders.*:$", line)))
