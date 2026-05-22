import pandas as pd
import json
import os
import datetime as dt 
import sys
import numpy as np
import importlib
import re


class DataSet:
    """Dataset class for loading data from files and directories into a normalized DataFrame."""
    def __init__(self, input_path, method=pd.read_csv, format=dict):
        """Load one file or all files under a directory into a path-to-data mapping."""

        data = {}
        if os.path.isdir(input_path):
            
            for f,g,h in os.walk(input_path):
                data.update({os.path.join(f, file).replace('\\','/'): method(os.path.join(f, file)) for file in h })

        elif os.path.isfile(input_path):
            data = {input_path: method(input_path)}
        
        self.data_dict = data

        """Convert a path-to-data mapping into a normalized DataFrame."""
        data_keys = [d.replace('\\','/') for d in data.keys()]
        prefix = input_path.replace('\\','/')
        data_df = pd.DataFrame({'path':data_keys, 'data':data.values()})
        data_df['prefix'] = prefix
        data_df['suffix'] = data_df['path'].apply(lambda x: x.split(prefix)[-1])

        self.data_df = data_df
