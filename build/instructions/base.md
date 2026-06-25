# BaseApp Agent Instructions
## These instructions apply when working on the base app

These instructions define how you must work on BaseApp.

## Task Selection

If you're asked to work on a specific task, create a task for anything you're asked to do, using the task template in `build/tasks/template.json`. Create the task in `build/tasks/base.json`.

If you're asked to work on any open task, follow the task list in `build/tasks/base.json`. Prioritize tasks by priority and progress.
Keep working until there are no open tasks. 

## For every task you're working on:

Change the task's status from `ToDo` to `InProgress` when you start working on it. Then commit and push the tasks file so that the task's status will be reflected in the repository.

Update the task list when significant progress is made on a task.
Use the task comments to update progress details.

## Branch Strategy: one short-lived branch per task

Do not do task work directly on the long-lived `main` branch. Each task is worked on in its own short-lived, ad-hoc branch so that concurrent tasks never mix their changes together.

1. When a task is started (moving it from `ToDo` to `InProgress`), it is worked on in its own dedicated short-lived branch named after the task id, e.g. `task/BASE-TASK-260624-0001`, branched off the latest `main`. You (the agent) do **not** create this branch yourself: the launcher `scripts/launch_task_agent.ps1` creates and checks it out (via its `-TaskBranch` parameter) before your session starts, after first verifying whether such a branch already exists and whether starting a worker is necessary. If a branch already exists, the task may already be (or have been) worked on, so the Task Manager does not silently start another worker: it asks the engineer to confirm, and the launcher proceeds only when the engineer opts in. By the time you begin you are already on the task branch — just confirm you are not on `main`.
2. Do all of the task's work — requirements, architecture, implementation, tests, comments, and the task-file status updates — on that branch, and push the branch (not `main`) when you commit and push.
3. The branch is short-lived: it exists only for the duration of the task and is dissolved (deleted) once the task is merged into `main` and reaches the `Done` status.
4. The merge of the task branch into `main` is supervised by the integration engineer, who makes sure there are no conflicts that must be resolved before the merge can complete successfully. Do not merge into `main` and dissolve the branch yourself; that happens at the `Done` step, under the integration engineer's supervision.

## Task Lifecycle: ToDo -> InProgress -> Ready -> Deployed -> Done

Tasks move through five steps. 

1. *Task Creation* Creates the task and specifies the title, description, type, priority, and additional attributes. This is done by the engineer who can use the UI, a powershell call, or manually edit the tasks file. At the end of this process, the status of the task is `ToDo`.

2. *Task Specifying*: This step is triggered by the engineer, and accomplished by you. If you are asked to work on a task:
        a. Change the status of the task to `InProgress`.
        b. Make sure that you are working on the dedicated branch for the task and not on the `main` branch. You do **not** create this branch — the launcher `scripts/launch_task_agent.ps1` already created and checked out `task/<task-id>` (via its `-TaskBranch` parameter) before your session began, after verifying whether the branch already existed and whether starting a worker was necessary (asking the engineer to confirm when a branch already existed). Just verify you are on the task branch.
        c. Add a comment to the task's comments list that you started working on the task.        
        d. Commit and push the tasks file (on the task branch) to the repository in order to reflect the status of the task globally. 
        e. Task Breakdown: break down each task into simple sub-tasks, including requirements specification tasks, architcture adjustment tasks, solution design tasks, implementation tasks, testing tasks, and deployment verification tasks. Execute the sub-tasks one by one. There is no need to document the simpler tasks in the tasks file. This is mainly for better agentic flow. But you can update in the comments about each task what you achieved and that would reflect the task breakdown.
        **Begin working on systems engineering:**
        f. Complete requirements specification. Requirements Engineering Guidance: For every task that includes a system enhancement or modification:
                f1 Clearly state the system requirement in the appropriate location in the hierarchy of requirements in `build/requirements/base.json`
                f2 Follow the requirements engineering instructions in `build/architecture/req-eng-instructions.md`
                f3 For maintenance requirements, there is typically no need to change the requirements or the architecture, unless it's clearly stated that the fix requires architecture changes.
        g. Complete architecture specification / modifications:
                g1 Use `build/architecture/base.json` for BaseApp items including folders. Use `build/architecture/app.json` for Variant App items and placeholders for future extension by the variant app designer. Identifiers must be unique across both files. For example, the `app` folder is listed as Feature #1 in `build/architecture/base.json` and as feature #1 in `build/architecture/app.json` without additional details (only for scaffolding) except for its sub-features. Then `app.py` is listed as Feature #1.2 in `build/architecture/app.json` under Feature #1. There is a separate mechanism for reconciliation between the two files.
                g2 The architecture file is a structured hierarchical list of app features. A feature is defined as a functional or structural characteristic.        
                g3 Each node in the architecture under the `features` node is a `feature_id`: `feature_dict` pair for complex features or a `feature_id`: `feature_name` for simple features. 
                g4 A complex feature's feature_dict includes `name`, `path`, `description`, `type`, and nested `features`.
                g5 The `feature_id` is a hierarchical dot-chained enumeration of items. For example, the `feature_id` of the `Logger` class in the `baseutils` package is `6.1.9` because it is item #9 in item #1 in item #6 in the app's directory. 
                g6 The `name` is a hierachical dot-chained name string that represents the lineage of the item. For example, the Logger's name is "utils.baseutils.Logger" because it sits in the baseutils package which sits in the utils sub-directory.
                g7 The `path` is an accurate relative path under the app folder. beyond the file name, items in the file are added to the path after a `::` separator.
                g8 If multiple files in a folder have the same basename add the extension with an underscore to avoid confusing dots for separators. for example if the same folder has `base.json` and `base.csv` used `base_json` and `base_csv`.
                g9 The `description` is a concise summay of the feature, which can also be adapted from the docstring. There is no need to include every nuance and every secondary or tertiary specific refinment or enhancement - just the main and core functionality/behavior. Details can be provided in the descriptions of sub-features.
                g10 The `type` is a dot-chained type string that represents the type lineage (combination of Folder, PyFile, JsonFile, MdFile, CsvFile, Class, Method, Function, DataSet, etc.) For example, the logger's type is `Folder.PyFile.Class` becasue it is a class inside a python file inside a folder.
                g11 Features can be hierarchical: Each feature can contain other features.
                g12 The number of items in the type chain must match the number of items in the name chain. do not add sub-types unless they map to named features.
                g13 As you update the feature list, you should also add the identifier of the feature as a comment in the file but only if the format permits it in a way that does not change the semantics of the file. For example, python code supports comments, but json and csv files do not. MD files support comments but these comments are considered part of the text that the reader sees.
                g14 Use the architecture/temp items to update the archiecture based on suggested corrections made by the architecture compliance test.
                g15 Architecting and Design Guidance:
                        1. Maximize reuse and avoid changing the architecture unless it is necessary and the more sensible way to implement the change.
                        2. Adhere to object oriented design practices and guidelines.
                        3. Store all the parameters in the appropriate configuration files. Avoid hard coded parameters.
                        4. Provide good clear documentation in docstrings for each package, class, method, function. Use comments to provide clear documentation for functional code blocks (no need for every line of code but mostly for main code blocks).
                        5. When logging, utilize logger functionality - use message codes instead of text, if necessary add new message codes to `messages/base.csv` or `messages/test.csv`. report variable values using data and not in message.
        h. Complete solution design. Specify which artifacts are going to be modified and how: which new files/classes/functions will be created and what will they do and how will they do it.
        i. Display the following to the engineer:
                d1. Modifications to the Requirements spec (`build/requirements/{base_or_app}.json`)
                d2. Modifications to the Architecture spec (`build/architecture/{base_or_app}.json`)
                d3. Intended Modifications to the Code (design) - what and how each aspect of the task will be implemented.
        j. Change the status of the task to `Specified`.
        k. Save the session summary to `f'{OUTPUT_PREFIX}/task/summaries/{task_id}/{task_id}.html'`. This summary has to include the task's instruction file (instance of `task.md`), and hyperlinks to the files that were modified in diff view, along with a textual explanation of what was changed and why (if it's not trivial).
        l. Wait for the engineer to authorize your solution specification (requirements, architecture, design).
        m. The engineer may ask you to change the specification or make manual changes that you need to track and respect.

3. *Task Implementing*: This step is triggered by the engineer, and accomplished by you. If you are asked to work on a task:
        After the engineer approves the specification, you can continue to implementation based on the engineer-approved design, which may be different from your initial design.
        n. Complete building the solution according to the approved design.
        o. Design, build, and run tests for the required feature. This includes running the `test/tests/build.py` script to execute the build tests, which include the build phase and app running in order to execute the prep/live/post tests once for the app. Maintain three tests for each feature if necessary and applicable: pre-test, live test, and post-test. Tests can be combined with other tests to simplify the test system. For example, if a single variable or object is sufficient for two separate tests, we can do away with a single test and two success criteria to match the two tests. Each test has to report which feature(s) it covers.
        p. Update the relevant `build/updates/{base_or_app}.json` file with the prgoress that you made.
        q. Add a progress report to the task's comments list.        
        r. Create a list of modified files and present them to the user. The files must be clickable and diff-reviewable. You can use a call to Visual Studio CODE to load the files in diff view to visualize the changes you made in each file, or you can create your own diff view in HTML and use the built-in HTML generator to create and store the file that shows the diff. Use a diff tool/package to visualize the changes clearly.
        s. Change the status of the task to `Ready`.

**Await the engineer's review and approval of the implementation.**

4. *Task Outcome Deployment*: after the engineer has reviewed and approved the change, and **Only after explicit user approval** 
When asked to finalize and deploy the code change (i.e. to move the status of the task from `Ready` to `Deployed`):

perform the Change Finalizing and Deployment steps below:
        a. set the status to `Approved`.
        b. append a builder approval comment to the task's comments list.
        c. bump the versions of the BaseApp (`resources/version/base.txt`) and placeholder for the variant app (`resources/version/app.txt`) as well as the config files that contain the version. 
                Update `resources/version/app.txt` to have the same value as the latest `resources/version/base.txt` but with the prefix letter `A`, so that future instantiations of new apps will start from the latest base version and then increment separately. For example if the base version is `26.05.27.03` then the app version will be `A26.05.27.03`
                Update the `config/base.json` file `COMMON` dict to include the latest version id.
        d. update the BaseApp's readme file to include the latest changes: `docs/readme/base.md`
                Update the updates/base.json file updates array to include a name and description of the change. The name structure has to be "[{APP_NAME}] [{Task_ID}] [{Feature_ID}] [{Feature}] {change desctiption}. New updates should be prepended to tthe top of the list.
        e. update the Main `README.md` file to provide a high-level functionality/capability overview of any added or modified features, functionalities, behaviors, etc. without going into implementation details.
                `README.md` will not detail implementation like you do in the `readme/base.md` or `readme/app.md` files. **Focus on functionality and capbility as it is now and not on the evolution of the software through the various changes and updates.**       
        f. Ask the engineer for a final approval to stage/commit/push the changes to the repository. 
        **When the engineer approves the push:**
        g. Append a comment on the task to indicate user approval.
        h. Change the status of the task to `Deployed`.
        i. Save the transcript of your work to `f'{OUTPUT_PREFIX}/task_reports/{task_id}/{task_id}.html'`.
        j. Stage and Commit the tasks file together with all the changes with a concise but informative commit message that includes the task id and essence of change.
        k. Push the changes to the git repository **on the task's short-lived branch** (not directly to `main`).

5. *Task Outcome Integration*: This step includes merging the task's ad-hoc branch into the `main` branch.
        When asked to finalize the change (i.e. to move the status of the task from `Deployed` to `Done`):
        a. Ask the user (integration engineer) to review the documentation about the change as well as the code, run the app on the task branch and does whatever is necessary to make sure the functionality is implemented correctly, and supervise the merge of the task's branch into `main`, making sure there are no conflicts to solve in order to complete the merge successfully. Resolve or suggest resolutions to any conflicts surfaced during the merge before proceeding.
        When final merge approval is granted:
        b. Merge the task's branch into `main`.
        c. set the status of the task to `Done`.
        d. append a deployment comment to the task's comments list noting that the task branch was merged into `main` and dissolved.        
        e. Commit the tasks file on `main` and push.
        f. Delete (dissolve) the short-lived task branch, since it is no longer needed.
        g. Update the task report at `f'{OUTPUT_PREFIX}/task_reports/{task_id}/{task_id}.html'`.
        