"""Verify the accepted snapshot once (manifest, every file hash, publication ledger) with the pinned backend.

Writes results/preflight.json with the stat fingerprint that every stage compares before and after."""
import json
import time

from .core import CONFIG, ROOT, SNAPSHOT, SNAPSHOT_ID, backend, cpu_s, stat_fingerprint, free_gib, git_state


def main():
    storage, _, _ = backend()
    fp0 = stat_fingerprint()
    t, c = time.monotonic(), cpu_s()
    checked = storage.verified(SNAPSHOT, SNAPSHOT_ID, ledger='require')
    verify_s = round(time.monotonic() - t, 2)
    manifest = checked['manifest']
    if manifest['manifest_sha256'] != CONFIG['input']['manifest_sha256']:
        raise SystemExit('manifest hash differs from the accepted snapshot')
    fp1 = stat_fingerprint()
    if fp0 != fp1:
        raise SystemExit('snapshot changed during verification')
    out = {
        'snapshot_id': SNAPSHOT_ID, 'manifest_sha256': manifest['manifest_sha256'],
        'publication': checked['publication'], 'verify_wall_s': verify_s, 'verify_cpu_s': round(cpu_s() - c, 2),
        'payload_bytes': manifest['snapshot_payload_bytes'], 'counts': manifest['counts'],
        'backend_pin': CONFIG['input']['backend_commit'], 'code': git_state(), 'free_gib': round(free_gib(), 1),
        'verified_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'stat_fingerprint': fp1,
    }
    (ROOT / 'results').mkdir(exist_ok=True)
    (ROOT / 'results' / 'preflight.json').write_text(json.dumps(out, indent=1) + '\n')
    print(json.dumps({k: v for k, v in out.items() if k != 'stat_fingerprint'}, indent=1))


if __name__ == '__main__':
    main()
