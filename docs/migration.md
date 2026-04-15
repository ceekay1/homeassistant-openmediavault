Migration notes — OMV 7 (breaking)

This document explains what users and maintainers need to know when upgrading from OpenMediaVault 5/6 to OMV 7 for use with this integration.

Summary
- OMV 7 introduced breaking RPC/field changes. This branch and the released integration code on the `omv7-api` branch require OMV 7.
- No Home Assistant configuration changes are required; the integration's configuration and entity IDs should remain the same where possible.

What changed (high level)
- `System.getInformation` fields renamed/changed: e.g. `cpuUtilization`, `memUtilization`, `availablePkgUpdates`, `configDirty`, `rebootRequired`.
- Disk management and SMART RPCs changed the device/attribute field names (e.g. `devicename`, `canonicaldevicefile`, SMART `attrname`, `threshold`, `rawvalue`).
- Services now return richer objects (`name`, `title`, `enabled`, `running`) instead of simple status strings.

User migration steps
1. Upgrade your OpenMediaVault instance to OMV 7 following OMV project docs.
2. In Home Assistant:
   - Restart Home Assistant after upgrading OMV.
   - If the integration does not reconnect automatically, remove and re-add the integration (Integration -> OpenMediaVault -> configure).
3. Verify sensors:
   - Check `System`/`Hardware` sensors for CPU/memory usage and update availability.
   - Check disk and SMART sensors for device metadata and attribute values.
4. If you see missing sensors or mismatched attributes, enable debug logging for `custom_components.openmediavault` and collect logs for troubleshooting.

Developer/maintainer notes
- The `omv_controller.py`, `omv_api.py`, and `apiparser.py` contain inline RPC→field mapping comments — review them when troubleshooting.
- `sensor_types.py` and `binary_sensor_types.py` were updated to map new OMV 7 keys; verify mappings if adding new entities.
- No HA-side config keys changed; entity IDs were preserved where feasible, but some attribute names changed.

Rollback guidance
- If you must stay on OMV 5/6, use a release/branch that targets OMV 5/6 rather than this `omv7-api` branch.

Troubleshooting & logs
- Enable debug logging in `configuration.yaml`:
```
logger:
  default: info
  logs:
    custom_components.openmediavault: debug
```
- Collect the logs and compare the RPC responses in the `omv_controller` debug output to confirm which fields the NAS is returning.

Contact/PRs
- If you encounter OMV 7 API variants not covered by the mappings, open an issue or PR with sample RPC responses and suggested mappings.
