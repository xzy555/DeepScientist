# WhatsApp Connector Enablement Log

This file records the work to promote WhatsApp from an implemented-but-not-default path into a first-class connector in DeepScientist.

## Goal

- Make WhatsApp visible by default in the system connector gate.
- Preserve the existing technical implementation:
  - `WhatsAppLocalSessionService`
  - `GenericRelayChannel`
  - `WhatsAppConnectorBridge`
- Add public-facing documentation so WhatsApp setup is discoverable like QQ, Weixin, and Telegram.

## Progress

- Created English and Chinese root-level logs for this change set.
- Enabled WhatsApp in the default system connector visibility gate.
- Updated the default-config test so WhatsApp is now expected to be system-enabled by default.
- Added a dedicated WhatsApp connector guide in English and Chinese.
- Added WhatsApp guide links to the public docs index and top-level README.
- Fixed the shared generic-profile save race in the Settings UI so newly created WhatsApp profiles are saved with the exact draft payload instead of relying on potentially stale React state.

## Files Changed

- `src/deepscientist/config/models.py`
- `tests/test_init_and_quest.py`
- `docs/en/17_WHATSAPP_CONNECTOR_GUIDE.md`
- `docs/zh/17_WHATSAPP_CONNECTOR_GUIDE.md`
- `docs/en/README.md`
- `docs/zh/README.md`
- `README.md`
- `src/ui/src/components/settings/SettingsPage.tsx`
- `src/ui/src/components/settings/ConnectorSettingsForm.tsx`

## Verification

- `python3 -m compileall src/deepscientist/config/models.py tests/test_init_and_quest.py`

## Verification Gaps

- No focused `pytest` execution was possible in the current environment because `pytest` is not installed.

## Notes

- This change set intentionally reuses the existing WhatsApp local-session/runtime path instead of introducing a new specialized channel.
