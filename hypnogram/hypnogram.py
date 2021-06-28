import numpy as np
import pandas as pd
from pathlib import Path


class Hypnogram(pd.DataFrame):
    def __init__(self, *args, **kwargs):
        super(Hypnogram, self).__init__(*args, **kwargs)

    def _validate(self):
        if not {"state", "start_time", "end_time", "duration"}.issubset(self):
            raise AttributeError()

    @property
    def _constructor(self):
        return Hypnogram

    def keep_states(self, states):
        """Return all bouts of the given states.

        Parameters:
        -----------
        states: list of str
        """
        return self[self["state"].isin(states)]

    def mask_times_by_state(self, times, states):
        """Return a mask that is true where times belong to specific states.

        Parameters
        ----------
        times: (n_times,)
            The times to mask.
        states: list of str
            The states of interest.

        Returns
        -------
        (n_times,)
            True where `times` belong to one of the indicated states, false otherise.
        """
        mask = np.full_like(times, False, dtype=bool)
        for bout in self.keep_states(states).itertuples():
            mask[(times >= bout.start_time) & (times <= bout.end_time)] = True

        return mask

    def get_states(self, times):
        """Given an array of times, label each time with its state.

        Parameters:
        -----------
        times: (n_times,)
            The times to label.

        Returns:
        --------
        states (n_times,)
            The state label for each sample in `times`.
        """
        labels = pd.Series([""] * len(times))
        for bout in self.itertuples():
            times_in_bout = (times >= bout.start_time) & (times <= bout.end_time)
            labels.values[times_in_bout] = bout.state

        return labels

    def covers_time(self, times):
        covered = np.full_like(times, False, dtype="bool")
        for bout in self.itertuples():
            times_in_bout = (times >= bout.start_time) & (times <= bout.end_time)
            covered[times_in_bout] = True

        return covered

    def write(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.to_csv(
            path,
            sep="\t",
            index=False,
        )


class FloatHypnogram(Hypnogram):
    @property
    def _constructor(self):
        return FloatHypnogram

    def write_visbrain(self, path):
        path(path).parent.mkdir(parents=True, exist_ok=True)
        self.to_csv(path, columns=["state", "end_time"], sep="\t", index=False)

    def as_datetime(self, start_datetime):
        df = self.copy()
        df["start_time"] = start_datetime + pd.to_timedelta(df["start_time"], "s")
        df["end_time"] = start_datetime + pd.to_timedelta(df["end_time"], "s")
        df["duration"] = pd.to_timedelta(df["duration"], "s")
        return DatetimeHypnogram(df)


class DatetimeHypnogram(Hypnogram):
    @property
    def _constructor(self):
        return DatetimeHypnogram

    def keep_first(self, cumulative_duration):
        """Keep hypnogram bouts until a cumulative duration is reached.

        Parameters:
        -----------
        cumulative_duration:
            Any valid timedelta specifier, `02:30:10` for 2h, 30m, 10s.
        """
        keep = self.duration.cumsum() <= pd.to_timedelta(cumulative_duration)
        return self.loc[keep]

    def keep_last(self, cumulative_duration):
        """Keep only a given amount of time at the end of a hypnogram.

        Parameters:
        -----------
        cumulative_duration:
            Any valid timedelta specifier, `02:30:10` for 2h, 30m, 10s.
        """
        keep = np.cumsum(self.duration[::-1])[::-1] <= pd.to_timedelta(
            cumulative_duration
        )
        return self.loc[keep]

    def keep_between(self, start_time, end_time):
        """Keep all hypnogram bouts that fall between two times of day.

        Paramters:
        ----------
        start_time:
            The starting hour, e.g. '13:00:00' for 1PM.
        end_time:
            The ending hour, e.g. '14:00:00' for 2PM.
        """
        keep = np.intersect1d(
            pd.DatetimeIndex(self.start_time).indexer_between_time(
                start_time, end_time
            ),
            pd.DatetimeIndex(self.end_time).indexer_between_time(start_time, end_time),
        )
        return self.iloc[keep]

    def keep_longer(self, duration):
        """Keep bouts longer than a given duration.

        Parameters:
        -----------
        duration:
            Any valid timedelta specifier, `02:30:10` for 2h, 30m, 10s.
        """
        return self[self["duration"] > pd.to_timedelta(duration)]

    def get_consolidated(self, states, frac=0.8, minimum_time="1M"):
        """Get periods of consolidated sleep, wake, or any arbitrary set of states.

        A period is considered consolidated if more than a given fraction of its duration
        (e.g. frac=0.8 or 80%) is spent in the state(s) of interest, and the cumulative
        amount of time spent in the state(s) of interest exceeds `minimum_time`.
        Additionally, a consolidated period must be maximal, i.e. it cannot be contained by
        a longer consolidated period.

        Parameters:
        -----------
        states: list of str
            The states of interest.
        frac: float between 0 and 1
            The minimum fraction of a given period that must be spent in the states of
            interest for that period to be considered consolidated.
        minimum_time: timedelta format string
            The minimum cumulative time that must be spent in the states of interest for
            a given period to be considered consolidated.

        Returns:
        --------
        matches: list of pd.DataFrame
            Each DataFrame is a slice of the hypnogram, corresponding to a consolidated
            period.
        """
        # This method would be easy to adapt for FloatHypnogram types.
        assert (
            self.start_time.is_monotonic_increasing
        ), "Hypnogram must be sorted by start_time."
        minimum_time = pd.to_timedelta(minimum_time)
        bouts = self.keep_states(states)
        k = bouts.index.min() - 1
        matches = list()
        # i = period start, j = period end, k = end of last consolidated period
        for i in bouts.index:
            if i <= k:  # if this period is contained in a longer good one, don't bother
                continue
            for j in bouts.index[::-1]:  # check periods in decreasing order of length
                if j < np.max([i, k]):  # don't both checking subperiods of good periods
                    break
                time_in_states = np.max(
                    [bouts.loc[i:j].duration.sum(), pd.to_timedelta(0, "s")]
                )
                if time_in_states < minimum_time:
                    break  # because all other periods in the loop will also fail
                total_time = (
                    self.loc[i:j].end_time.max() - self.loc[i:j].start_time.min()
                )
                if (time_in_states / total_time) >= frac:
                    matches.append(self.loc[i:j])
                    k = j
                    break  # don't both checking subperiods of good periods
        return matches

    def get_gaps(self, tolerance="0s"):
        """Get all unscored gaps in the hypnogram.

        Parameters:
        -----------
        tolterance: timedelta format string
            Optionally ignore gaps that are less than a given duration.

        Returns:
        --------
        gaps: list of dict
            Each gap detected, with start_time, end_time, and duration.
        """
        gaps = list()
        for i in range(len(self) - 1):
            current_bout_end = self.iloc[i].end_time
            next_bout_start = self.iloc[i + 1].start_time
            gap = next_bout_start - current_bout_end
            if gap > pd.to_timedelta(tolerance):
                gaps.append(
                    dict(
                        start_time=current_bout_end,
                        end_time=next_bout_start,
                        duration=gap,
                    )
                )

        return gaps

    def fill_gaps(self, tolerance="0s", fill_state="None"):
        """Fill all unscored gaps in the hypnogram with a specified state.

        Parameters:
        -----------
        tolerance: timedelta format string
            Optionally ignore gaps that are less than a given duration.
        fill_state: string
            The state to fill each gap with.

        Returns:
        --------
        hypnogram: DatetimeHypnogram
            The hypnogram, with gaps filled.
        """
        gaps = self.get_gaps(tolerance)
        for gap in gaps:
            gap.update({"state": fill_state})

        return self.append(gaps).sort_values("start_time", ignore_index=True)


def _infer_bout_start(df, bout):
    """Infer a bout's start time from the previous bout's end time.

    Parameters
    ----------
    h: DataFrame, (n_bouts, ?)
        Hypogram in Visbrain format with 'start_time'.
    row: Series
        A row from `h`, representing the bout that you want the start time of.

    Returns
    -------
    start_time: float
        The start time of the bout from `row`.
    """
    if bout.name == 0:
        start_time = 0.0
    else:
        start_time = df.loc[bout.name - 1].end_time

    return start_time


def load_visbrain_hypnogram(path):
    """Load a Visbrain formatted hypnogram."""
    df = pd.read_csv(path, sep="\t", names=["state", "end_time"], comment="*")
    df["start_time"] = df.apply(lambda row: _infer_bout_start(df, row), axis=1)
    df["duration"] = df.apply(lambda row: row.end_time - row.start_time, axis=1)
    return FloatHypnogram(df)


def load_spike2_hypnogram(path):
    """Load a Spike2 formatted hypnogram."""
    df = pd.read_table(
        path,
        sep="\t",
        names=["epoch", "start_time", "end_time", "state", "comment", "blank"],
        usecols=["epoch", "start_time", "end_time", "state"],
        index_col="epoch",
        skiprows=22,
    )
    return FloatHypnogram(df)


def load_datetime_hypnogram(path):
    """Load a hypnogram whose entries are valid datetime strings."""
    df = pd.read_csv(path, sep="\t")
    df["start_time"] = pd.to_datetime(df["start_time"])
    df["end_time"] = pd.to_datetime(df["end_time"])
    df["duration"] = pd.to_timedelta(df["duration"])
    return DatetimeHypnogram(df)


def get_empty_hypnogram(end_time):
    """Return an empty, unscored hypnogram.

    Parameters
    ----------
    end_time: float
        The time at which the hypnogram should end, in seconds.

    Returns:
        H: pd.DataFrame
            A hypnogram containing a single state ("None") extending from t=0 until `end_time`.
    """
    df = pd.DataFrame(
        {
            "state": "None",
            "start_time": [0.0],
            "end_time": [end_time],
            "duration": [end_time],
        }
    )
    return FloatHypnogram(df)


def get_separated_wake_hypnogram(qwk_intervals, awk_intervals):
    """Turn a list of quiet wake and active wake intervals into a hypnogram.

    Parameters
    ----------
    qwk_intervals: list(tuple(float))
        Start and end times of each quiet wake bout.
    awk_intervals: list(tuple(float))
        Start and end times of each quiet wake bout.
    """
    qwk_intervals = np.asarray(qwk_intervals)
    awk_intervals = np.asarray(awk_intervals)

    qwk = pd.DataFrame(
        {
            "state": "qWk",
            "start_time": qwk_intervals[:, 0],
            "end_time": qwk_intervals[:, 1],
            "duration": np.diff(qwk_intervals).flatten(),
        }
    )
    awk = pd.DataFrame(
        {
            "state": "aWk",
            "start_time": awk_intervals[:, 0],
            "end_time": awk_intervals[:, 1],
            "duration": np.diff(awk_intervals).flatten(),
        }
    )

    df = pd.concat([qwk, awk]).sort_values(by=["start_time"]).reset_index()
    return Hypnogram(df)
