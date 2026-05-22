# BaseApp Agent Instructions

These instructions define how coding agents should work on BaseApp.
0. break down each task into simple tasks, and execute the simpler tasks one by one.
1. Maximize reuse and avoid changing the architecture unless it is necessary and the more sensible way to implement the change.
2. Adhere to object oriented guidelines.
3. Update the version after every code change.
4. Update the readme base.md after every code change.
5. Update the config/base.json file updates array to include a name and description of the change. New updates should be up the list. Also, keep only the latest 10 updates.
6. Update the config/base.json file app dict to include the latest version id.
