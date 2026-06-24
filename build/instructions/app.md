# App Instructions
## These instructions apply when working on the variant app

These instructions define how you must work on a variant app of BaseApp.


## Task Selection

If you're asked to work on a specific task, create a task for anything you're asked to do, using the task template in `build/tasks/template.json`. Create the task in `build/tasks/app.json`.

If you're asked to work on any open task, follow the task list in `build/tasks/app.json`. Prioritize tasks by priority and progress.
Keep working until there are no open tasks. 

## For every task you're working on:

Prioritize tasks by priority and progress.
Update the task list when significant progress is made on a task.
Use the task comments to update progress details.

For every task that includes a system enhancement or modification:

Clearly state the system requirement in the appropriate location in the hierarchy of requirements in `build/requirements/app.json`

Follow the requirements engineering instructions in `build/architecture/req-eng-instructions.md`

For maintenance requirements, there is typically no need to change the requirements or the architecture, unless it's clearly stated that the fix requires architecture changes.

Follow all the instructions in `build/instructions/base.md` but notice that wherever the instructions refer to a base resource (e.g. `app/base.py`, `config/base.json`, etc.) - switch here to the corresponding app resource ( `app/app.py`, `config/app.json`, etc. )

Follow the same task lifecycle as the base app: `ToDo` -> `InProgress` -> `Ready` -> `Done`. When you finish the work, set the task to `Ready`, commit the changes to the architecture, requirements, tests, implementation but do not update the versioning and documentation yet.
After engineer approval only move it to `Done`, finalize, commit, and push after the engineer reviews and approves.

Avoid modifications to base app artifacts, these should not be modified by the app. If behavior changes are needed, inherit and override in app artifacts (`app.py`,`apputils.py`, `app.json`, etc.)

When logging, utilize logger functionality - use message codes instead of text, if necessary add new message codes to `messages/app.csv`. report variable values using data and not in message.

If a generic functionality is needed, submit a task into the `build/tasks/base.json` file so that the functionality will be implemented as a base functionality and be available to all apps utilizing this framework.

Update the app's README.md file and use it to provide an overview of the app and its capabilities, functionalities, and features.