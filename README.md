### MobileApp

Mobile app data storage

### Installation

You can install this app using the [bench](https://github.com/frappe/bench) CLI:

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app $URL_OF_THIS_REPO --branch develop
bench install-app mobile_app
```

### AI chat integration

AI chat is served by `wa_chat_hub.api.mobile_app`; this app supplies the existing
`Mobile App User` and profile records. The sync API contract is unchanged.

Deploy the latest chat identity fix in `wa_chat_hub` with this app. The
`configure_ai_chat_phone_region` migration sets `mobile_app_ai_phone_region` to
`IN` when no region or explicit-country mode has already been configured.
It does not alter phone numbers, profile links or chat history. Restart the site
processes after deploying and migrating so they load the new configuration.

For an existing site, the configuration patch can also be applied independently:

```bash
bench --site <site> execute mobile_app.patches.v1_0.configure_ai_chat_phone_region.execute
```

### Contributing

This app uses `pre-commit` for code formatting and linting. Please [install pre-commit](https://pre-commit.com/#installation) and enable it for this repository:

```bash
cd apps/mobile_app
pre-commit install
```

Pre-commit is configured to use the following tools for checking and formatting your code:

- ruff
- eslint
- prettier
- pyupgrade

### License

mit
