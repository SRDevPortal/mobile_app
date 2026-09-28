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

AI chat is served by `wa_chat_hub.api.mobile_app`. Every channel with Channel Type
`Mobile App` uses the authenticated account and selected profile, without phone
or country-code matching. Deploy the matching `wa_chat_hub` schema and code,
run the site migration, and restart web/worker processes.

When syncing existing profiles, echo the ERP profile row `name` returned by the
API. Use that same value as chat `profile_id`; the server preserves IDs belonging
to the user and rejects foreign or repeated IDs. Omit `name` only for new profiles.
Phone-owned history remains stored and is not automatically claimed by an app
account. See `wa_chat_hub/docs/MOBILE_APP_CHAT_IDENTITY.md` for integration details.

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
