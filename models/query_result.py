from dataclasses import dataclass
from typing import Optional

import pandas as pd


@dataclass
class QueryResult:

    rows: Optional[pd.DataFrame]

    row_count: int

    execution_ms: float

    error: Optional[str] = None