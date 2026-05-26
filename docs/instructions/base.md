# BaseApp Agent Instructions
## These instructions apply when working on the base app

These instructions define how coding agents should work on BaseApp.
0. break down each task into simple tasks, and execute the simpler tasks one by one.
1. Maximize reuse and avoid changing the architecture unless it is necessary and the more sensible way to implement the change.
2. Adhere to object oriented guidelines.
3. Increment the version id when first starting to make changes after repo sync. No need to create a new version number for each small change.
4. Provide good documentation in docstrings for each class, method, function, etc. and for functional code blocks (no need for every line of code) 
5. Update the readme base.md after every code change.
6. Update the config/base.json file updates array to include a name and description of the change. The name structure has to be "[{APP_NAME}] {Feature}. New updates should be up the list. Also, keep only the latest 10 versions (but keep all the updates of each version that is kept).
7. Update the config/base.json file app dict to include the latest version id.
8. Update docs/version/app.txt to have the same value as the latest docs/version/base.txt so that future instantiations of new apps will start from the latest base version and then increment separately.
9. When logging, utilize logger functionality - use message codes instead of text, if necessary add new message codes to messages/base.csv. report variable values using data and not in message.