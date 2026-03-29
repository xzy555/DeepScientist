# Telegram Connector Enablement Log

This file records the work to promote Telegram from an implemented-but-not-default path into a first-class connector in DeepScientist.

## Goal

- Make Telegram visible by default in the system connector gate.
- Preserve the existing technical implementation:
  - `TelegramPollingService`
  - `GenericRelayChannel`
  - `TelegramConnectorBridge`
- Add public-facing documentation so Telegram setup is discoverable in the same way as QQ and Weixin.

## Progress

- Created English and Chinese root-level logs for this change set.
- Enabled Telegram in the default system connector visibility gate.
- Updated default-config tests so Telegram is now expected to be system-enabled by default.
- Added a dedicated Telegram connector guide in English and Chinese.
- Added Telegram guide links to the public docs index and top-level README.
- Fixed a shared generic-profile save race in the Settings UI so newly created Telegram profiles are saved with the exact draft payload instead of relying on potentially stale React state.

## Files Changed

- `src/deepscientist/config/models.py`
- `tests/test_init_and_quest.py`
- `tests/test_generic_connectors.py`
- `docs/en/16_TELEGRAM_CONNECTOR_GUIDE.md`
- `docs/zh/16_TELEGRAM_CONNECTOR_GUIDE.md`
- `docs/en/README.md`
- `docs/zh/README.md`
- `README.md`
- `src/ui/src/components/settings/SettingsPage.tsx`
- `src/ui/src/components/settings/ConnectorSettingsForm.tsx`

## Verification

- `python3 -m compileall src/deepscientist/config/models.py tests/test_init_and_quest.py tests/test_generic_connectors.py`

## Verification Gaps

- Focused `pytest` runs were attempted, but the current environment does not have `pytest` installed.

## Notes

- This change set intentionally reuses the existing Telegram polling/runtime path instead of introducing a new specialized Telegram channel.
