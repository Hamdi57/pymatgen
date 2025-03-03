"""This module implements a FloatWithUnit, which is a subclass of float. It
also defines supported units for some commonly used units for energy, length,
temperature, time and charge. FloatWithUnit also support conversion to one
another, and additions and subtractions perform automatic conversion if
units are detected. An ArrayWithUnit is also implemented, which is a subclass
of numpy's ndarray with similar unit features.
"""

from __future__ import annotations

import collections
import numbers
import re
from functools import partial
from typing import Any

import numpy as np
import scipy.constants as const

__author__ = "Shyue Ping Ong, Matteo Giantomassi"
__copyright__ = "Copyright 2011, The Materials Project"
__version__ = "1.0"
__maintainer__ = "Shyue Ping Ong, Matteo Giantomassi"
__status__ = "Production"
__date__ = "Aug 30, 2013"

"""Some conversion factors"""
Ha_to_eV = 1 / const.physical_constants["electron volt-hartree relationship"][0]
eV_to_Ha = 1 / Ha_to_eV
Ry_to_eV = Ha_to_eV / 2
amu_to_kg = const.physical_constants["atomic mass unit-kilogram relationship"][0]
mile_to_meters = const.mile
bohr_to_angstrom = const.physical_constants["Bohr radius"][0] * 1e10
bohr_to_ang = bohr_to_angstrom
ang_to_bohr = 1 / bohr_to_ang
kCal_to_kJ = const.calorie
kb = const.physical_constants["Boltzmann constant in eV/K"][0]

"""
Definitions of supported units. Values below are essentially scaling and
conversion factors. What matters is the relative values, not the absolute.
The SI units must have factor 1.
"""
BASE_UNITS = {
    "length": {
        "m": 1,
        "km": 1000,
        "mile": mile_to_meters,
        "ang": 1e-10,
        "cm": 1e-2,
        "pm": 1e-12,
        "bohr": bohr_to_angstrom * 1e-10,
    },
    "mass": {
        "kg": 1,
        "g": 1e-3,
        "amu": amu_to_kg,
    },
    "time": {
        "s": 1,
        "min": 60,
        "h": 3600,
        "d": 3600 * 24,
    },
    "current": {"A": 1},
    "temperature": {
        "K": 1,
    },
    "amount": {"mol": 1, "atom": 1 / const.N_A},
    "intensity": {"cd": 1},
    "memory": {
        "byte": 1,
        "Kb": 1024,
        "Mb": 1024**2,
        "Gb": 1024**3,
        "Tb": 1024**4,
    },
}

# Accept kb, mb, gb ... as well.
BASE_UNITS["memory"].update({k.lower(): v for k, v in BASE_UNITS["memory"].items()})

# This current list are supported derived units defined in terms of powers of
# SI base units and constants.
DERIVED_UNITS: dict[str, dict] = {
    "energy": {
        "eV": {"kg": 1, "m": 2, "s": -2, const.e: 1},
        "meV": {"kg": 1, "m": 2, "s": -2, const.e * 1e-3: 1},
        "Ha": {"kg": 1, "m": 2, "s": -2, const.e * Ha_to_eV: 1},
        "Ry": {"kg": 1, "m": 2, "s": -2, const.e * Ry_to_eV: 1},
        "J": {"kg": 1, "m": 2, "s": -2},
        "kJ": {"kg": 1, "m": 2, "s": -2, 1000: 1},
        "kCal": {"kg": 1, "m": 2, "s": -2, 1000: 1, kCal_to_kJ: 1},
    },
    "charge": {
        "C": {"A": 1, "s": 1},
        "e": {"A": 1, "s": 1, const.e: 1},
    },
    "force": {
        "N": {"kg": 1, "m": 1, "s": -2},
        "KN": {"kg": 1, "m": 1, "s": -2, 1000: 1},
        "MN": {"kg": 1, "m": 1, "s": -2, 1e6: 1},
        "GN": {"kg": 1, "m": 1, "s": -2, 1e9: 1},
    },
    "frequency": {
        "Hz": {"s": -1},
        "KHz": {"s": -1, 1000: 1},
        "MHz": {"s": -1, 1e6: 1},
        "GHz": {"s": -1, 1e9: 1},
        "THz": {"s": -1, 1e12: 1},
    },
    "pressure": {
        "Pa": {"kg": 1, "m": -1, "s": -2},
        "KPa": {"kg": 1, "m": -1, "s": -2, 1000: 1},
        "MPa": {"kg": 1, "m": -1, "s": -2, 1e6: 1},
        "GPa": {"kg": 1, "m": -1, "s": -2, 1e9: 1},
    },
    "power": {
        "W": {"m": 2, "kg": 1, "s": -3},
        "KW": {"m": 2, "kg": 1, "s": -3, 1000: 1},
        "MW": {"m": 2, "kg": 1, "s": -3, 1e6: 1},
        "GW": {"m": 2, "kg": 1, "s": -3, 1e9: 1},
    },
    "emf": {"V": {"m": 2, "kg": 1, "s": -3, "A": -1}},
    "capacitance": {"F": {"m": -2, "kg": -1, "s": 4, "A": 2}},
    "resistance": {"ohm": {"m": 2, "kg": 1, "s": -3, "A": -2}},
    "conductance": {"S": {"m": -2, "kg": -1, "s": 3, "A": 2}},
    "magnetic_flux": {"Wb": {"m": 2, "kg": 1, "s": -2, "A": -1}},
    "cross_section": {"barn": {"m": 2, 1e-28: 1}, "mbarn": {"m": 2, 1e-31: 1}},
}

ALL_UNITS: dict[str, dict] = BASE_UNITS | DERIVED_UNITS
SUPPORTED_UNIT_NAMES = tuple(i for d in ALL_UNITS.values() for i in d)

# Mapping unit name --> unit type (unit names must be unique).
_UNAME2UTYPE = {uname: utype for utype, dct in ALL_UNITS.items() for uname in dct}


def _get_si_unit(unit):
    unit_type = _UNAME2UTYPE[unit]
    si_unit = filter(lambda k: BASE_UNITS[unit_type][k] == 1, BASE_UNITS[unit_type])
    return next(iter(si_unit)), BASE_UNITS[unit_type][unit]


class UnitError(BaseException):
    """Exception class for unit errors."""


def _check_mappings(u):
    for v in DERIVED_UNITS.values():
        for k2, v2 in v.items():
            if all(v2.get(ku, 0) == vu for ku, vu in u.items()) and all(
                u.get(kv2, 0) == vv2 for kv2, vv2 in v2.items()
            ):
                return {k2: 1}
    return u


class Unit(collections.abc.Mapping):
    """Represents a unit, e.g., "m" for meters, etc. Supports compound units.
    Only integer powers are supported for units.
    """

    Error = UnitError

    def __init__(self, unit_def) -> None:
        """Constructs a unit.

        Args:
            unit_def: A definition for the unit. Either a mapping of unit to
                powers, e.g., {"m": 2, "s": -1} represents "m^2 s^-1",
                or simply as a string "kg m^2 s^-1". Note that the supported
                format uses "^" as the power operator and all units must be
                space-separated.
        """
        if isinstance(unit_def, str):
            unit: dict[str, int] = collections.defaultdict(int)

            for match in re.finditer(r"([A-Za-z]+)\s*\^*\s*([\-0-9]*)", unit_def):
                val = match.group(2)
                val = 1 if not val else int(val)
                key = match.group(1)
                unit[key] += val
        else:
            unit = {k: v for k, v in dict(unit_def).items() if v != 0}
        self._unit = _check_mappings(unit)

    def __mul__(self, other):
        new_units = collections.defaultdict(int)
        for k, v in self.items():
            new_units[k] += v
        for k, v in other.items():
            new_units[k] += v
        return Unit(new_units)

    def __truediv__(self, other):
        new_units = collections.defaultdict(int)
        for k, v in self.items():
            new_units[k] += v
        for k, v in other.items():
            new_units[k] -= v
        return Unit(new_units)

    def __pow__(self, i):
        return Unit({k: v * i for k, v in self.items()})

    def __iter__(self):
        return iter(self._unit)

    def __getitem__(self, i) -> int:
        return self._unit[i]

    def __len__(self) -> int:
        return len(self._unit)

    def __repr__(self) -> str:
        sorted_keys = sorted(self._unit, key=lambda k: (-self._unit[k], k))
        return " ".join(
            [f"{k}^{self._unit[k]}" if self._unit[k] != 1 else k for k in sorted_keys if self._unit[k] != 0]
        )

    @property
    def as_base_units(self):
        """Converts all units to base SI units, including derived units.

        Returns:
            (base_units_dict, scaling factor). base_units_dict will not
            contain any constants, which are gathered in the scaling factor.
        """
        b = collections.defaultdict(int)
        factor = 1
        for k, v in self.items():
            derived = False
            for d in DERIVED_UNITS.values():
                if k in d:
                    for k2, v2 in d[k].items():
                        if isinstance(k2, numbers.Number):
                            factor *= k2 ** (v2 * v)
                        else:
                            b[k2] += v2 * v
                    derived = True
                    break
            if not derived:
                si, f = _get_si_unit(k)
                b[si] += v
                factor *= f**v
        return {k: v for k, v in b.items() if v != 0}, factor

    def get_conversion_factor(self, new_unit):
        """Returns a conversion factor between this unit and a new unit.
        Compound units are supported, but must have the same powers in each
        unit type.

        Args:
            new_unit: The new unit.
        """
        old_base, old_factor = self.as_base_units
        new_base, new_factor = Unit(new_unit).as_base_units
        units_new = sorted(new_base.items(), key=lambda d: _UNAME2UTYPE[d[0]])
        units_old = sorted(old_base.items(), key=lambda d: _UNAME2UTYPE[d[0]])
        factor = old_factor / new_factor
        for old, new in zip(units_old, units_new):
            if old[1] != new[1]:
                raise UnitError(f"Units {old} and {new} are not compatible!")
            c = ALL_UNITS[_UNAME2UTYPE[old[0]]]
            factor *= (c[old[0]] / c[new[0]]) ** old[1]
        return factor


class FloatWithUnit(float):
    """Subclasses float to attach a unit type. Typically, you should use the
    pre-defined unit type subclasses such as Energy, Length, etc. instead of
    using FloatWithUnit directly.

    Supports conversion, addition and subtraction of the same unit type. E.g.,
    1 m + 20 cm will be automatically converted to 1.2 m (units follow the
    leftmost quantity). Note that FloatWithUnit does not override the eq
    method for float, i.e., units are not checked when testing for equality.
    The reason is to allow this class to be used transparently wherever floats
    are expected.

    >>> e = Energy(1.1, "Ha")
    >>> a = Energy(1.1, "Ha")
    >>> b = Energy(3, "eV")
    >>> c = a + b
    >>> print(c)
    1.2102479761938871 Ha
    >>> c.to("eV")
    32.932522246000005 eV
    """

    Error = UnitError

    @classmethod
    def from_str(cls, s):
        """Parse string to FloatWithUnit.

        Example: Memory.from_str("1. Mb")
        """
        # Extract num and unit string.
        s = s.strip()
        for idx, char in enumerate(s):  # noqa: B007
            if char.isalpha() or char.isspace():
                break
        else:
            raise Exception(f"Unit is missing in string {s}")
        num, unit = float(s[:idx]), s[idx:]

        # Find unit type (set it to None if it cannot be detected)
        for unit_type, d in BASE_UNITS.items():  # noqa: B007
            if unit in d:
                break
        else:
            unit_type = None

        return cls(num, unit, unit_type=unit_type)

    def __new__(cls, val, unit, unit_type=None):
        """Overrides __new__ since we are subclassing a Python primitive/."""
        new = float.__new__(cls, val)
        new._unit = Unit(unit)
        new._unit_type = unit_type
        return new

    def __init__(self, val, unit, unit_type=None) -> None:
        """Initializes a float with unit.

        Args:
            val (float): Value
            unit (Unit): A unit. E.g., "C".
            unit_type (str): A type of unit. E.g., "charge"
        """
        if unit_type is not None and str(unit) not in ALL_UNITS[unit_type]:
            raise UnitError(f"{unit} is not a supported unit for {unit_type}")
        self._unit = Unit(unit)
        self._unit_type = unit_type

    def __str__(self) -> str:
        return f"{super().__str__()} {self._unit}"

    def __add__(self, other):
        if not hasattr(other, "unit_type"):
            return super().__add__(other)
        if other.unit_type != self._unit_type:
            raise UnitError("Adding different types of units is not allowed")
        val = other
        if other.unit != self._unit:
            val = other.to(self._unit)
        return FloatWithUnit(float(self) + val, unit_type=self._unit_type, unit=self._unit)

    def __sub__(self, other):
        if not hasattr(other, "unit_type"):
            return super().__sub__(other)
        if other.unit_type != self._unit_type:
            raise UnitError("Subtracting different units is not allowed")
        val = other
        if other.unit != self._unit:
            val = other.to(self._unit)
        return FloatWithUnit(float(self) - val, unit_type=self._unit_type, unit=self._unit)

    def __mul__(self, other):
        if not isinstance(other, FloatWithUnit):
            return FloatWithUnit(float(self) * other, unit_type=self._unit_type, unit=self._unit)
        return FloatWithUnit(float(self) * other, unit_type=None, unit=self._unit * other._unit)

    def __rmul__(self, other):
        if not isinstance(other, FloatWithUnit):
            return FloatWithUnit(float(self) * other, unit_type=self._unit_type, unit=self._unit)
        return FloatWithUnit(float(self) * other, unit_type=None, unit=self._unit * other._unit)

    def __pow__(self, i):
        return FloatWithUnit(float(self) ** i, unit_type=None, unit=self._unit**i)

    def __truediv__(self, other):
        val = super().__truediv__(other)
        if not isinstance(other, FloatWithUnit):
            return FloatWithUnit(val, unit_type=self._unit_type, unit=self._unit)
        return FloatWithUnit(val, unit_type=None, unit=self._unit / other._unit)

    def __neg__(self):
        return FloatWithUnit(super().__neg__(), unit_type=self._unit_type, unit=self._unit)

    def __getnewargs__(self):
        """Function used by pickle to recreate object."""
        # TODO There's a problem with _unit_type if we try to unpickle objects from file.
        # since self._unit_type might not be defined. I think this is due to
        # the use of decorators (property and unitized). In particular I have problems with "amu"
        # likely due to weight in core.composition
        if hasattr(self, "_unit_type"):
            args = float(self), self._unit, self._unit_type
        else:
            args = float(self), self._unit, None

        return args

    def __getstate__(self):
        state = self.__dict__.copy()
        state["val"] = float(self)
        return state

    def __setstate__(self, state):
        self._unit = state["_unit"]

    @property
    def unit_type(self) -> str:
        """The type of unit. Energy, Charge, etc."""
        return self._unit_type

    @property
    def unit(self) -> Unit:
        """The unit, e.g., "eV"."""
        return self._unit

    def to(self, new_unit):
        """Conversion to a new_unit. Right now, only supports 1 to 1 mapping of
        units of each type.

        Args:
            new_unit: New unit type.

        Returns:
            A FloatWithUnit object in the new units.

        Example usage:
        >>> e = Energy(1.1, "eV")
        >>> e = Energy(1.1, "Ha")
        >>> e.to("eV")
        29.932522246 eV
        """
        return FloatWithUnit(
            self * self.unit.get_conversion_factor(new_unit),
            unit_type=self._unit_type,
            unit=new_unit,
        )

    @property
    def as_base_units(self):
        """Returns this FloatWithUnit in base SI units, including derived units.

        Returns:
            A FloatWithUnit object in base SI units
        """
        return self.to(self.unit.as_base_units[0])

    @property
    def supported_units(self):
        """Supported units for specific unit type."""
        return tuple(ALL_UNITS[self._unit_type])


class ArrayWithUnit(np.ndarray):
    """Subclasses numpy.ndarray to attach a unit type. Typically, you should
    use the pre-defined unit type subclasses such as EnergyArray,
    LengthArray, etc. instead of using ArrayWithFloatWithUnit directly.

    Supports conversion, addition and subtraction of the same unit type. E.g.,
    1 m + 20 cm will be automatically converted to 1.2 m (units follow the
    leftmost quantity).

    >>> a = EnergyArray([1, 2], "Ha")
    >>> b = EnergyArray([1, 2], "eV")
    >>> c = a + b
    >>> print(c)
    [ 1.03674933  2.07349865] Ha
    >>> c.to("eV")
    array([ 28.21138386,  56.42276772]) eV
    """

    Error = UnitError

    def __new__(cls, input_array, unit, unit_type=None):
        """Override __new__."""
        # Input array is an already formed ndarray instance
        # We first cast to be our class type
        obj = np.asarray(input_array).view(cls)
        # add the new attributes to the created instance
        obj._unit = Unit(unit)
        obj._unit_type = unit_type
        return obj

    def __array_finalize__(self, obj):
        """See http://docs.scipy.org/doc/numpy/user/basics.subclassing.html for
        comments.
        """
        if obj is None:
            return
        self._unit = getattr(obj, "_unit", None)
        self._unit_type = getattr(obj, "_unit_type", None)

    @property
    def unit_type(self) -> str:
        """The type of unit. Energy, Charge, etc."""
        return self._unit_type

    @property
    def unit(self) -> str:
        """The unit, e.g., "eV"."""
        return self._unit

    def __reduce__(self):
        reduce = list(super().__reduce__())
        reduce[2] = {"np_state": reduce[2], "_unit": self._unit}
        return tuple(reduce)

    def __setstate__(self, state):
        super().__setstate__(state["np_state"])
        self._unit = state["_unit"]

    def __repr__(self) -> str:
        return f"{np.array(self)!r} {self.unit}"

    def __str__(self) -> str:
        return f"{np.array(self)} {self.unit}"

    def __add__(self, other):
        if hasattr(other, "unit_type"):
            if other.unit_type != self.unit_type:
                raise UnitError("Adding different types of units is not allowed")

            if other.unit != self.unit:
                other = other.to(self.unit)

        return self.__class__(np.array(self) + np.array(other), unit_type=self.unit_type, unit=self.unit)

    def __sub__(self, other):
        if hasattr(other, "unit_type"):
            if other.unit_type != self.unit_type:
                raise UnitError("Subtracting different units is not allowed")

            if other.unit != self.unit:
                other = other.to(self.unit)

        return self.__class__(np.array(self) - np.array(other), unit_type=self.unit_type, unit=self.unit)

    def __mul__(self, other):
        # TODO Here we have the most important difference between FloatWithUnit and
        # ArrayWithFloatWithUnit:
        # If other does not have units, I return an object with the same units
        # as self.
        # if other *has* units, I return an object *without* units since
        # taking into account all the possible derived quantities would be
        # too difficult.
        # Moreover Energy(1.0) * Time(1.0, "s") returns 1.0 Ha that is a
        # bit misleading.
        # Same protocol for __div__
        if not hasattr(other, "unit_type"):
            return self.__class__(
                np.array(self) * np.array(other),
                unit_type=self._unit_type,
                unit=self._unit,
            )
        # Cannot use super since it returns an instance of self.__class__
        # while here we want a bare numpy array.
        return self.__class__(np.array(self).__mul__(np.array(other)), unit=self.unit * other.unit)

    def __rmul__(self, other):
        if not hasattr(other, "unit_type"):
            return self.__class__(
                np.array(self) * np.array(other),
                unit_type=self._unit_type,
                unit=self._unit,
            )
        return self.__class__(np.array(self) * np.array(other), unit=self.unit * other.unit)

    def __truediv__(self, other):
        if not hasattr(other, "unit_type"):
            return self.__class__(np.array(self) / np.array(other), unit_type=self._unit_type, unit=self._unit)
        return self.__class__(np.array(self) / np.array(other), unit=self.unit / other.unit)

    def __neg__(self):
        return self.__class__(-np.array(self), unit_type=self.unit_type, unit=self.unit)

    def to(self, new_unit):
        """Conversion to a new_unit.

        Args:
            new_unit:
                New unit type.

        Returns:
            A ArrayWithFloatWithUnit object in the new units.

        Example usage:
        >>> e = EnergyArray([1, 1.1], "Ha")
        >>> e.to("eV")
        array([ 27.21138386,  29.93252225]) eV
        """
        return self.__class__(
            np.array(self) * self.unit.get_conversion_factor(new_unit),
            unit_type=self.unit_type,
            unit=new_unit,
        )

    @property
    def as_base_units(self):
        """Returns this ArrayWithUnit in base SI units, including derived units.

        Returns:
            An ArrayWithUnit object in base SI units
        """
        return self.to(self.unit.as_base_units[0])

    # TODO abstract base class property?
    @property
    def supported_units(self):
        """Supported units for specific unit type."""
        return ALL_UNITS[self.unit_type]

    # TODO abstract base class method?
    def conversions(self):
        """Returns a string showing the available conversions.
        Useful tool in interactive mode.
        """
        return "\n".join(str(self.to(unit)) for unit in self.supported_units)


def _my_partial(func, *args, **kwargs):
    """Partial returns a partial object and therefore we cannot inherit class
    methods defined in FloatWithUnit. This function calls partial and patches
    the new class before returning.
    """
    newobj = partial(func, *args, **kwargs)
    # monkey patch
    newobj.from_str = FloatWithUnit.from_str
    return newobj


Energy = partial(FloatWithUnit, unit_type="energy")
"""
A float with an energy unit.

Args:
    val (float): Value
    unit (Unit): E.g., eV, kJ, etc. Must be valid unit or UnitError is raised.
"""
EnergyArray = partial(ArrayWithUnit, unit_type="energy")

Length = partial(FloatWithUnit, unit_type="length")
"""
A float with a length unit.

Args:
    val (float): Value
    unit (Unit): E.g., m, ang, bohr, etc. Must be valid unit or UnitError is
        raised.
"""
LengthArray = partial(ArrayWithUnit, unit_type="length")

Mass = partial(FloatWithUnit, unit_type="mass")
"""
A float with a mass unit.

Args:
    val (float): Value
    unit (Unit): E.g., amu, kg, etc. Must be valid unit or UnitError is
        raised.
"""
MassArray = partial(ArrayWithUnit, unit_type="mass")

Temp = partial(FloatWithUnit, unit_type="temperature")
"""
A float with a temperature unit.

Args:
    val (float): Value
    unit (Unit): E.g., K. Only K (kelvin) is supported.
"""
TempArray = partial(ArrayWithUnit, unit_type="temperature")

Time = partial(FloatWithUnit, unit_type="time")
"""
A float with a time unit.

Args:
    val (float): Value
    unit (Unit): E.g., s, min, h. Must be valid unit or UnitError is
        raised.
"""
TimeArray = partial(ArrayWithUnit, unit_type="time")

Charge = partial(FloatWithUnit, unit_type="charge")
"""
A float with a charge unit.

Args:
    val (float): Value
    unit (Unit): E.g., C, e (electron charge). Must be valid unit or UnitError
        is raised.
"""
ChargeArray = partial(ArrayWithUnit, unit_type="charge")

Memory = _my_partial(FloatWithUnit, unit_type="memory")
"""
A float with a memory unit.

Args:
    val (float): Value
    unit (Unit): E.g., Kb, Mb, Gb, Tb. Must be valid unit or UnitError
        is raised.
"""


def obj_with_unit(obj: Any, unit: str) -> FloatWithUnit | ArrayWithUnit | dict[str, FloatWithUnit | ArrayWithUnit]:
    """Returns a FloatWithUnit instance if obj is scalar, a dictionary of
    objects with units if obj is a dict, else an instance of
    ArrayWithFloatWithUnit.

    Args:
        obj (Any): Object to be given a unit.
        unit (str): Specific units (eV, Ha, m, ang, etc.).
    """
    unit_type = _UNAME2UTYPE[unit]

    if isinstance(obj, numbers.Number):
        return FloatWithUnit(obj, unit=unit, unit_type=unit_type)
    if isinstance(obj, collections.abc.Mapping):
        return {k: obj_with_unit(v, unit) for k, v in obj.items()}  # type: ignore
    return ArrayWithUnit(obj, unit=unit, unit_type=unit_type)


def unitized(unit):
    """Useful decorator to assign units to the output of a function. You can also
    use it to standardize the output units of a function that already returns
    a FloatWithUnit or ArrayWithUnit. For sequences, all values in the sequences
    are assigned the same unit. It works with Python sequences only. The creation
    of numpy arrays loses all unit information. For mapping types, the values
    are assigned units.

    Args:
        unit: Specific unit (eV, Ha, m, ang, etc.).

    Example:
        @unitized(unit="kg")
        def get_mass():
            return 123.45
    """

    def wrap(func):
        def wrapped_f(*args, **kwargs):
            val = func(*args, **kwargs)
            unit_type = _UNAME2UTYPE[unit]

            if isinstance(val, (FloatWithUnit, ArrayWithUnit)):
                return val.to(unit)

            if isinstance(val, collections.abc.Sequence):
                # TODO: why don't we return a ArrayWithUnit?
                # This complicated way is to ensure the sequence type is
                # preserved (list or tuple).
                return val.__class__([FloatWithUnit(i, unit_type=unit_type, unit=unit) for i in val])
            if isinstance(val, collections.abc.Mapping):
                for k, v in val.items():
                    val[k] = FloatWithUnit(v, unit_type=unit_type, unit=unit)
            elif isinstance(val, numbers.Number):
                return FloatWithUnit(val, unit_type=unit_type, unit=unit)
            elif val is None:
                pass
            else:
                raise TypeError(f"Don't know how to assign units to {val}")
            return val

        return wrapped_f

    return wrap
