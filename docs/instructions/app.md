# App Instructions
## These instructions apply when working on the variant app

These instructions define how coding agents should work on variant app.
0. break down each task into simple tasks, and execute the simpler tasks one by one.
1. Maximize reuse and avoid changing the architecture unless it is necessary and the more sensible way to implement the change.
2. Adhere to object oriented guidelines.
3. Update the version in docs/version/app.txt for every code change.
4. Update the readme app.md after every code change.
5. Update the config/app.json file updates array to include a name and description of the change and the new version id. The name structure has to be "[{APP_NAME}] {Feature} New updates should be up the list. Also, keep only the latest 10 updates.
6. Update the config/app.json file app dict to include the latest version id.
7. Avoid modifications to base app artifacts, these should not be modified by the app. If behavior changes are needed, inherit and override in app artifacts (app.py,apputils.py, app.json, etc.)
8. When logging, utilize logger functionality - use message codes instead of text, if necessary add new message codes to messages/app.csv. report variable values using data and not in message.
