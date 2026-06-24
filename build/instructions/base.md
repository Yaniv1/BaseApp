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

## Task Lifecycle: ToDo -> InProgress -> Ready -> Done

Tasks move through four states. Never jump straight from InProgress to Done.

1. `ToDo`: the task has been defined but has not been started.
2. `InProgress`: you, the engineer, or a combination of users and agents are actively working on the task. If you are asked to work on a task:
        a. Change the status of the task to `InProgress`
        b. Add a comment to the task's comments list that you started working on the task.
        c. Commit and push the tasks file to the repository in order to reflect the status of the task globally.
        
3. `Ready`: you have completed your work on the task, which includes:
        a. You completed requirements specification.
        b. You completed architecture specification / modifications and solution design.
        c. You successfully complted implementation of the architecural changes and solution design.
        d. You successfully complete designing, building, and running tests for the required feature. This includes running the `test/tests/build.py` script to execute the build tests, which include the build phase and app running in order to execute the prep/live/post tests once for the app.
        e. You updated the updates file with the prgoress that you made.
        f. You added a progress report to the task's comments list.        
        g. You changed the status of the task to `Ready`
        h. You create a list of modified files and present them to the user. The files must be clickable and diff-reviewable. You can use a call to Visual Studio CODE to load the files in diff view to visualize the changes you made in each file, or you can create your own diff view in HTML and use the built-in HTML generator to create and store the file that shows the diff. Use a diff tool/package to visualize the changes clearly.

- Task Breakdown: break down each task into simple sub-tasks, including requirements specification tasks, architcture adjustment tasks, solution design tasks, implementation tasks, testing tasks, and deployment verification tasks.
Execute the sub-tasks one by one. There is no need to document the simpler tasks in the tasks file. This is mainly for better agentic flow. But you can update in the comments about each task what you achieved and that would reflect the task breakdown.

- Requirements Engineering Guidance:
        For every task that includes a system enhancement or modification:
        Clearly state the system requirement in the appropriate location in the hierarchy of requirements in `build/requirements/base.json`
        Follow the requirements engineering instructions in `build/architecture/req-eng-instructions.md`
        For maintenance requirements, there is typically no need to change the requirements or the architecture, unless it's clearly stated that the fix requires architecture changes.

- Architecture specification
        Use `build/architecture/base.json` for BaseApp items including folders. Use `build/architecture/app.json` for Variant App items and placeholders for future extension by the variant app designer. Identifiers must be unique across both files. For example, the `app` folder is listed as Feature #1 in `build/architecture/base.json` and as feature #1 in `build/architecture/app.json` without additional details (only for scaffolding) except for its sub-features. Then `app.py` is listed as Feature #1.2 in `build/architecture/app.json` under Feature #1. There is a separate mechanism for reconciliation between the two files.
        The architecture file is a structured hierarchical list of app features. A feature is defined as a functional or structural characteristic.        
        Each node in the architecture under the `features` node is a `feature_id`: `feature_dict` pair for complex features or a `feature_id`: `feature_name` for simple features. 
        A complex feature's feature_dict includes `name`, `path`, `description`, `type`, and nested `features`.
        The `feature_id` is a hierarchical dot-chained enumeration of items. For example, the `feature_id` of the `Logger` class in the `baseutils` package is `6.1.9` because it is item #9 in item #1 in item #6 in the app's directory. 
        The `name` is a hierachical dot-chained name string that represents the lineage of the item. For example, the Logger's name is "utils.baseutils.Logger" because it sits in the baseutils package which sits in the utils sub-directory.
        The `path` is an accurate relative path under the app folder. beyond the file name, items in the file are added to the path after a `::` separator.
        If multiple files in a folder have the same basename add the extension with an underscore to avoid confusing dots for separators. for example if the same folder has `base.json` and `base.csv` used `base_json` and `base_csv`.
        The `description` is a concise summay of the feature, which can also be adapted from the docstring. There is no need to include every nuance and every secondary or tertiary specific refinment or enhancement - just the main and core functionality/behavior. Details can be provided in the descriptions of sub-features.
        The `type` is a dot-chained type string that represents the type lineage (combination of Folder, PyFile, JsonFile, MdFile, CsvFile, Class, Method, Function, DataSet, etc.) For example, the logger's type is `Folder.PyFile.Class` becasue it is a class inside a python file inside a folder.
        Features can be hierarchical: Each feature can contain other features.
        The number of items in the type chain must match the number of items in the name chain. do not add sub-types unless they map to named features.
        As you update the feature list, you should also add the identifier of the feature as a comment in the file but only if the format permits it in a way that does not change the semantics of the file. For example, python code supports comments, but json and csv files do not. MD files support comments but these comments are considered part of the text that the reader sees.
        Use the architecture/temp items to update the archiecture based on suggested corrections made by the architecture compliance test.

- Architecting and Design Guidance:
        1. Maximize reuse and avoid changing the architecture unless it is necessary and the more sensible way to implement the change.
        2. Adhere to object oriented design practices and guidelines.
        3. Store all the parameters in the appropriate configuration files. Avoid hard coded parameters.
        4. Provide good clear documentation in docstrings for each package, class, method, function. Use comments to provide clear documentation for functional code blocks (no need for every line of code but mostly for main code blocks).
        5. When logging, utilize logger functionality - use message codes instead of text, if necessary add new message codes to `messages/base.csv` or `messages/test.csv`. report variable values using data and not in message.
        
- Test Design, Building, and Execution Guidance:
        Maintain three tests for each feature if necessary and applicable: pre-test, live test, and post-test. 
        Tests can be combined with other tests to simplify the test system. 
        For example, if a single variable or object is sufficient for two separate tests, we can do away with a single test and two success criteria to match the two tests. Each test has to report which feature(s) it covers.

**Await the engineer's review and approval.**

4. `Done`: after the engineer has reviewed and approved the change, **Only after explicit user approval** 
When asked to finalize and deploy the code change (i.e. to move the status of the task from `Ready` to `Done`):

perform the Change Finalizing and Deployment steps below:
        a. set the status to `Done`.
        b. append a builder approval comment to the task's comments list.
        c. bump the versions of the BaseApp (`resources/version/base.txt`) and placeholder for the variant app (`resources/version/app.txt`) as well as the config files that contain the version. 
                Update `resources/version/app.txt` to have the same value as the latest `resources/version/base.txt` but with the prefix letter `A`, so that future instantiations of new apps will start from the latest base version and then increment separately. For example if the base version is `26.05.27.03` then the app version will be `A26.05.27.03`
                Update the `config/base.json` file `COMMON` dict to include the latest version id.
        d. update the BaseApp's readme file to include the latest changes: `docs/readme/base.md`
                Update the updates/base.json file updates array to include a name and description of the change. The name structure has to be "[{APP_NAME}] [{Task_ID}] [{Feature_ID}] [{Feature}] {change desctiption}. New updates should be prepended to tthe top of the list.
        e. update the Main `README.md` file to provide a high-level functionality/capability overview of any added or modified features, functionalities, behaviors, etc. without going into implementation details.
                `README.md` will not detail implementation like you do in the `readme/base.md` or `readme/app.md` files. **Focus on functionality and capbility as it is now and not on the evolution of the software through the various changes and updates.**

        f. Stage and Commit the tasks file together with all the changes with a concise but informative commit message that includes the task id and essence of change.
        g. Push the changes to the git repository.
        h. Save the transcript of your work to `f'{OUTPUT_PREFIX}/agent_transcripts/{task_id}/{task_id}.html'`.



