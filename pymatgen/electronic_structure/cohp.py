"""This module defines classes to represent crystal orbital Hamilton
populations (COHP) and integrated COHP (ICOHP), but can also be used
for crystal orbital overlap populations (COOP) or crystal orbital bond indices (COBIs).
If you use this module, please cite:
J. George, G. Petretto, A. Naik, M. Esters, A. J. Jackson, R. Nelson, R. Dronskowski, G.-M. Rignanese, G. Hautier,
"Automated Bonding Analysis with Crystal Orbital Hamilton Populations",
ChemPlusChem 2022, e202200123,
DOI: 10.1002/cplu.202200123.
"""

from __future__ import annotations

import re
import sys
import warnings

import numpy as np
from monty.json import MSONable
from scipy.interpolate import InterpolatedUnivariateSpline

from pymatgen.core.sites import PeriodicSite
from pymatgen.core.structure import Structure
from pymatgen.electronic_structure.core import Orbital, Spin
from pymatgen.io.lmto import LMTOCopl
from pymatgen.io.lobster import Cohpcar
from pymatgen.util.coord import get_linear_interpolated_value
from pymatgen.util.due import Doi, due
from pymatgen.util.num import round_to_sigfigs

__author__ = "Marco Esters, Janine George"
__copyright__ = "Copyright 2017, The Materials Project"
__version__ = "0.2"
__maintainer__ = "Janine George"
__email__ = "janinegeorge.ulfen@gmail.com"
__date__ = "Dec 13, 2017"

due.cite(
    Doi("10.1002/cplu.202200123"),
    description="Automated Bonding Analysis with Crystal Orbital Hamilton Populations",
)


class Cohp(MSONable):
    """Basic COHP object."""

    def __init__(self, efermi, energies, cohp, are_coops=False, are_cobis=False, icohp=None) -> None:
        """
        Args:
            are_coops: Indicates whether this object describes COOPs.
            are_cobis: Indicates whether this object describes COBIs.
            efermi: Fermi energy.
            energies: A sequence of energies.
            cohp ({Spin: np.array}): representing the COHP for each spin.
            icohp ({Spin: np.array}): representing the ICOHP for each spin.
        """
        self.are_coops = are_coops
        self.are_cobis = are_cobis
        self.efermi = efermi
        self.energies = np.array(energies)
        self.cohp = cohp
        self.icohp = icohp

    def __repr__(self) -> str:
        """Returns a string that can be easily plotted (e.g. using gnuplot)."""
        if self.are_coops:
            cohp_str = "COOP"
        elif self.are_cobis:
            cohp_str = "COBI"
        else:
            cohp_str = "COHP"
        header = ["Energy", f"{cohp_str}Up"]
        data = [self.energies, self.cohp[Spin.up]]
        if Spin.down in self.cohp:
            header.append(f"{cohp_str}Down")
            data.append(self.cohp[Spin.down])
        if self.icohp:
            header.append(f"I{cohp_str}Up")
            data.append(self.icohp[Spin.up])
            if Spin.down in self.cohp:
                header.append(f"I{cohp_str}Down")
                data.append(self.icohp[Spin.down])
        format_header = "#" + " ".join("{:15s}" for __ in header)
        format_data = " ".join("{:.5f}" for __ in header)
        str_arr = [format_header.format(*header)]
        for idx in range(len(self.energies)):
            str_arr.append(format_data.format(*(d[idx] for d in data)))
        return "\n".join(str_arr)

    def as_dict(self):
        """JSON-serializable dict representation of COHP."""
        dct = {
            "@module": type(self).__module__,
            "@class": type(self).__name__,
            "are_coops": self.are_coops,
            "are_cobis": self.are_cobis,
            "efermi": self.efermi,
            "energies": self.energies.tolist(),
            "COHP": {str(spin): pops.tolist() for spin, pops in self.cohp.items()},
        }
        if self.icohp:
            dct["ICOHP"] = {str(spin): pops.tolist() for spin, pops in self.icohp.items()}
        return dct

    def get_cohp(self, spin=None, integrated=False):
        """Returns the COHP or ICOHP for a particular spin.

        Args:
            spin: Spin. Can be parsed as spin object, integer (-1/1)
                or str ("up"/"down")
            integrated: Return COHP (False) or ICOHP (True)

        Returns:
            Returns the CHOP or ICOHP for the input spin. If Spin is
            None and both spins are present, both spins will be returned
            as a dictionary.
        """
        populations = self.cohp if not integrated else self.icohp

        if populations is None:
            return None
        if spin is None:
            return populations
        if isinstance(spin, int):
            spin = Spin(spin)
        elif isinstance(spin, str):
            spin = Spin({"up": 1, "down": -1}[spin.lower()])
        return {spin: populations[spin]}

    def get_icohp(self, spin=None):
        """Convenient alternative to get the ICOHP for a particular spin."""
        return self.get_cohp(spin=spin, integrated=True)

    def get_interpolated_value(self, energy, integrated=False):
        """Returns the COHP for a particular energy.

        Args:
            energy: Energy to return the COHP value for.
            integrated: Return COHP (False) or ICOHP (True)
        """
        inter = {}
        for spin in self.cohp:
            if not integrated:
                inter[spin] = get_linear_interpolated_value(self.energies, self.cohp[spin], energy)
            elif self.icohp is not None:
                inter[spin] = get_linear_interpolated_value(self.energies, self.icohp[spin], energy)
            else:
                raise ValueError("ICOHP is empty.")
        return inter

    def has_antibnd_states_below_efermi(self, spin=None, limit=0.01):
        """Returns dict indicating if there are antibonding states below the Fermi level depending on the spin
        spin: Spin
        limit: -COHP smaller -limit will be considered.
        """
        populations = self.cohp
        number_energies_below_efermi = len([x for x in self.energies if x <= self.efermi])

        if populations is None:
            return None
        if spin is None:
            dict_to_return = {}
            for sp, cohpvalues in populations.items():
                if (max(cohpvalues[0:number_energies_below_efermi])) > limit:
                    dict_to_return[sp] = True
                else:
                    dict_to_return[sp] = False
        else:
            dict_to_return = {}
            if isinstance(spin, int):
                spin = Spin(spin)
            elif isinstance(spin, str):
                s = {"up": 1, "down": -1}[spin.lower()]
                spin = Spin(s)
            if (max(populations[spin][0:number_energies_below_efermi])) > limit:
                dict_to_return[spin] = True
            else:
                dict_to_return[spin] = False

        return dict_to_return

    @classmethod
    def from_dict(cls, dct):
        """Returns a COHP object from a dict representation of the COHP."""
        icohp = {Spin(int(key)): np.array(val) for key, val in dct["ICOHP"].items()} if "ICOHP" in dct else None
        are_cobis = dct.get("are_cobis", False)
        return Cohp(
            dct["efermi"],
            dct["energies"],
            {Spin(int(key)): np.array(val) for key, val in dct["COHP"].items()},
            icohp=icohp,
            are_coops=dct["are_coops"],
            are_cobis=are_cobis,
        )


class CompleteCohp(Cohp):
    """A wrapper class that defines an average COHP, and individual COHPs.

    Attributes:
        are_coops (bool): Indicates whether the object is consisting of COOPs.
        are_cobis (bool): Indicates whether the object is consisting of COBIs.
        efermi (float): Fermi energy.
        energies (Sequence[float]): Sequence of energies.
        structure (pymatgen.Structure): Structure associated with the COHPs.
        cohp (Sequence[float]): The average COHP.
        icohp (Sequence[float]): The average ICOHP.
        all_cohps (dict[str, Sequence[float]]): A dict of COHPs for individual bonds of the form {label: COHP}.
        orb_res_cohp (dict[str, Dict[str, Sequence[float]]]): Orbital-resolved COHPs.
    """

    def __init__(
        self,
        structure,
        avg_cohp,
        cohp_dict,
        bonds=None,
        are_coops=False,
        are_cobis=False,
        orb_res_cohp=None,
    ) -> None:
        """
        Args:
            structure: Structure associated with this COHP.
            avg_cohp: The average cohp as a COHP object.
            cohp_dict: A dict of COHP objects for individual bonds of the form
                {label: COHP}
            bonds: A dict containing information on the bonds of the form
                {label: {key: val}}. The key-val pair can be any information
                the user wants to put in, but typically contains the sites,
                the bond length, and the number of bonds. If nothing is
                supplied, it will default to an empty dict.
            are_coops: indicates whether the Cohp objects are COOPs.
                Defaults to False for COHPs.
            are_cobis: indicates whether the Cohp objects are COBIs.
                Defaults to False for COHPs.
            orb_res_cohp: Orbital-resolved COHPs.
        """
        if are_coops and are_cobis:
            raise ValueError("You cannot have info about COOPs and COBIs in the same file.")
        super().__init__(
            avg_cohp.efermi,
            avg_cohp.energies,
            avg_cohp.cohp,
            are_coops=are_coops,
            are_cobis=are_cobis,
            icohp=avg_cohp.icohp,
        )
        self.structure = structure
        self.are_coops = are_coops
        self.are_cobis = are_cobis
        self.all_cohps = cohp_dict
        self.orb_res_cohp = orb_res_cohp
        self.bonds = bonds or {label: {} for label in self.all_cohps}

    def __str__(self) -> str:
        if self.are_coops:
            return f"Complete COOPs for {self.structure}"
        if self.are_cobis:
            return f"Complete COBIs for {self.structure}"
        return f"Complete COHPs for {self.structure}"

    def as_dict(self):
        """JSON-serializable dict representation of CompleteCohp."""
        dct = {
            "@module": type(self).__module__,
            "@class": type(self).__name__,
            "are_coops": self.are_coops,
            "are_cobis": self.are_cobis,
            "efermi": self.efermi,
            "structure": self.structure.as_dict(),
            "energies": self.energies.tolist(),
            "COHP": {"average": {str(spin): pops.tolist() for spin, pops in self.cohp.items()}},
        }

        if self.icohp is not None:
            dct["ICOHP"] = {"average": {str(spin): pops.tolist() for spin, pops in self.icohp.items()}}

        for label in self.all_cohps:
            dct["COHP"].update({label: {str(spin): pops.tolist() for spin, pops in self.all_cohps[label].cohp.items()}})
            if self.all_cohps[label].icohp is not None:
                if "ICOHP" not in dct:
                    dct["ICOHP"] = {
                        label: {str(spin): pops.tolist() for spin, pops in self.all_cohps[label].icohp.items()}
                    }
                else:
                    dct["ICOHP"].update(
                        {label: {str(spin): pops.tolist() for spin, pops in self.all_cohps[label].icohp.items()}}
                    )
        if False in [bond_dict == {} for bond_dict in self.bonds.values()]:
            dct["bonds"] = {
                bond: {
                    "length": self.bonds[bond]["length"],
                    "sites": [site.as_dict() for site in self.bonds[bond]["sites"]],
                }
                for bond in self.bonds
            }
        if self.orb_res_cohp:
            orb_dict = {}
            for label in self.orb_res_cohp:
                orb_dict[label] = {}
                for orbs in self.orb_res_cohp[label]:
                    cohp = {str(spin): pops.tolist() for spin, pops in self.orb_res_cohp[label][orbs]["COHP"].items()}
                    orb_dict[label][orbs] = {"COHP": cohp}
                    icohp = {str(spin): pops.tolist() for spin, pops in self.orb_res_cohp[label][orbs]["ICOHP"].items()}
                    orb_dict[label][orbs]["ICOHP"] = icohp
                    orbitals = [[orb[0], orb[1].name] for orb in self.orb_res_cohp[label][orbs]["orbitals"]]
                    orb_dict[label][orbs]["orbitals"] = orbitals
            dct["orb_res_cohp"] = orb_dict

        return dct

    def get_cohp_by_label(self, label, summed_spin_channels=False):
        """Get specific COHP object.

        Args:
            label: string (for newer Lobster versions: a number)
            summed_spin_channels: bool, will sum the spin channels and return the sum in Spin.up if true

        Returns:
            Returns the COHP object to simplify plotting
        """
        if label.lower() == "average":
            divided_cohp = self.cohp
            divided_icohp = self.icohp

        else:
            divided_cohp = self.all_cohps[label].get_cohp(spin=None, integrated=False)
            divided_icohp = self.all_cohps[label].get_icohp(spin=None)

        if summed_spin_channels and Spin.down in self.cohp:
            final_cohp = {}
            final_icohp = {}
            final_cohp[Spin.up] = np.sum([divided_cohp[Spin.up], divided_cohp[Spin.down]], axis=0)
            final_icohp[Spin.up] = np.sum([divided_icohp[Spin.up], divided_icohp[Spin.down]], axis=0)
        else:
            final_cohp = divided_cohp
            final_icohp = divided_icohp

        return Cohp(
            efermi=self.efermi,
            energies=self.energies,
            cohp=final_cohp,
            are_coops=self.are_coops,
            are_cobis=self.are_cobis,
            icohp=final_icohp,
        )

    def get_summed_cohp_by_label_list(self, label_list, divisor=1, summed_spin_channels=False):
        """Returns a COHP object that includes a summed COHP divided by divisor.

        Args:
            label_list: list of labels for the COHP that should be included in the summed cohp
            divisor: float/int, the summed cohp will be divided by this divisor
            summed_spin_channels: bool, will sum the spin channels and return the sum in Spin.up if true

        Returns:
            Returns a COHP object including a summed COHP
        """
        # check if cohps are spinpolarized or not
        first_cohpobject = self.get_cohp_by_label(label_list[0])
        summed_cohp = first_cohpobject.cohp.copy()
        summed_icohp = first_cohpobject.icohp.copy()
        for label in label_list[1:]:
            cohp_here = self.get_cohp_by_label(label)
            summed_cohp[Spin.up] = np.sum([summed_cohp[Spin.up], cohp_here.cohp[Spin.up]], axis=0)

            if Spin.down in summed_cohp:
                summed_cohp[Spin.down] = np.sum([summed_cohp[Spin.down], cohp_here.cohp[Spin.down]], axis=0)

            summed_icohp[Spin.up] = np.sum([summed_icohp[Spin.up], cohp_here.icohp[Spin.up]], axis=0)

            if Spin.down in summed_icohp:
                summed_icohp[Spin.down] = np.sum([summed_icohp[Spin.down], cohp_here.icohp[Spin.down]], axis=0)

        divided_cohp = {}
        divided_icohp = {}
        divided_cohp[Spin.up] = np.divide(summed_cohp[Spin.up], divisor)
        divided_icohp[Spin.up] = np.divide(summed_icohp[Spin.up], divisor)
        if Spin.down in summed_cohp:
            divided_cohp[Spin.down] = np.divide(summed_cohp[Spin.down], divisor)
            divided_icohp[Spin.down] = np.divide(summed_icohp[Spin.down], divisor)

        if summed_spin_channels and Spin.down in summed_cohp:
            final_cohp = {}
            final_icohp = {}
            final_cohp[Spin.up] = np.sum([divided_cohp[Spin.up], divided_cohp[Spin.down]], axis=0)
            final_icohp[Spin.up] = np.sum([divided_icohp[Spin.up], divided_icohp[Spin.down]], axis=0)
        else:
            final_cohp = divided_cohp
            final_icohp = divided_icohp

        return Cohp(
            efermi=first_cohpobject.efermi,
            energies=first_cohpobject.energies,
            cohp=final_cohp,
            are_coops=first_cohpobject.are_coops,
            are_cobis=first_cohpobject.are_coops,
            icohp=final_icohp,
        )

    def get_summed_cohp_by_label_and_orbital_list(
        self, label_list, orbital_list, divisor=1, summed_spin_channels=False
    ):
        """Returns a COHP object that includes a summed COHP divided by divisor.

        Args:
            label_list: list of labels for the COHP that should be included in the summed cohp
            orbital_list: list of orbitals for the COHPs that should be included in the summed cohp (same order as
                label_list)
            divisor: float/int, the summed cohp will be divided by this divisor
            summed_spin_channels: bool, will sum the spin channels and return the sum in Spin.up if true

        Returns:
            Returns a COHP object including a summed COHP
        """
        # check length of label_list and orbital_list:
        if not len(label_list) == len(orbital_list):
            raise ValueError("label_list and orbital_list don't have the same length!")
        # check if cohps are spinpolarized or not
        first_cohpobject = self.get_orbital_resolved_cohp(label_list[0], orbital_list[0])
        summed_cohp = first_cohpobject.cohp.copy()
        summed_icohp = first_cohpobject.icohp.copy()
        for ilabel, label in enumerate(label_list[1:], 1):
            cohp_here = self.get_orbital_resolved_cohp(label, orbital_list[ilabel])
            summed_cohp[Spin.up] = np.sum([summed_cohp[Spin.up], cohp_here.cohp.copy()[Spin.up]], axis=0)
            if Spin.down in summed_cohp:
                summed_cohp[Spin.down] = np.sum([summed_cohp[Spin.down], cohp_here.cohp.copy()[Spin.down]], axis=0)
            summed_icohp[Spin.up] = np.sum([summed_icohp[Spin.up], cohp_here.icohp.copy()[Spin.up]], axis=0)
            if Spin.down in summed_icohp:
                summed_icohp[Spin.down] = np.sum([summed_icohp[Spin.down], cohp_here.icohp.copy()[Spin.down]], axis=0)

        divided_cohp = {}
        divided_icohp = {}
        divided_cohp[Spin.up] = np.divide(summed_cohp[Spin.up], divisor)
        divided_icohp[Spin.up] = np.divide(summed_icohp[Spin.up], divisor)
        if Spin.down in summed_cohp:
            divided_cohp[Spin.down] = np.divide(summed_cohp[Spin.down], divisor)
            divided_icohp[Spin.down] = np.divide(summed_icohp[Spin.down], divisor)

        if summed_spin_channels and Spin.down in divided_cohp:
            final_cohp = {}
            final_icohp = {}

            final_cohp[Spin.up] = np.sum([divided_cohp[Spin.up], divided_cohp[Spin.down]], axis=0)
            final_icohp[Spin.up] = np.sum([divided_icohp[Spin.up], divided_icohp[Spin.down]], axis=0)
        else:
            final_cohp = divided_cohp
            final_icohp = divided_icohp

        return Cohp(
            efermi=first_cohpobject.efermi,
            energies=first_cohpobject.energies,
            cohp=final_cohp,
            are_coops=first_cohpobject.are_coops,
            are_cobis=first_cohpobject.are_cobis,
            icohp=final_icohp,
        )

    def get_orbital_resolved_cohp(self, label, orbitals, summed_spin_channels=False):
        """Get orbital-resolved COHP.

        Args:
            label: bond label (Lobster: labels as in ICOHPLIST/ICOOPLIST.lobster).

            orbitals: The orbitals as a label, or list or tuple of the form
                [(n1, orbital1), (n2, orbital2)]. Orbitals can either be str,
                int, or Orbital.

            summed_spin_channels: bool, will sum the spin channels and return the sum in Spin.up if true

        Returns:
            A Cohp object if CompleteCohp contains orbital-resolved cohp,
            or None if it doesn't.

        Note: It currently assumes that orbitals are str if they aren't the
            other valid types. This is not ideal, but the easiest way to
            avoid unicode issues between python 2 and python 3.
        """
        if self.orb_res_cohp is None:
            return None
        if isinstance(orbitals, (list, tuple)):
            cohp_orbs = [d["orbitals"] for d in self.orb_res_cohp[label].values()]
            orbs = []
            for orbital in orbitals:
                if isinstance(orbital[1], int):
                    orbs.append((orbital[0], Orbital(orbital[1])))
                elif isinstance(orbital[1], Orbital):
                    orbs.append((orbital[0], orbital[1]))
                elif isinstance(orbital[1], str):
                    orbs.append((orbital[0], Orbital[orbital[1]]))
                else:
                    raise TypeError("Orbital must be str, int, or Orbital.")
            orb_index = cohp_orbs.index(orbs)
            orb_label = list(self.orb_res_cohp[label])[orb_index]
        elif isinstance(orbitals, str):
            orb_label = orbitals
        else:
            raise TypeError("Orbitals must be str, list, or tuple.")
        try:
            icohp = self.orb_res_cohp[label][orb_label]["ICOHP"]
        except KeyError:
            icohp = None

        start_cohp = self.orb_res_cohp[label][orb_label]["COHP"]
        start_icohp = icohp

        if summed_spin_channels and Spin.down in start_cohp:
            final_cohp = {}
            final_icohp = {}
            final_cohp[Spin.up] = np.sum([start_cohp[Spin.up], start_cohp[Spin.down]], axis=0)
            if start_icohp is not None:
                final_icohp[Spin.up] = np.sum([start_icohp[Spin.up], start_icohp[Spin.down]], axis=0)
        else:
            final_cohp = start_cohp
            final_icohp = start_icohp

        return Cohp(
            self.efermi,
            self.energies,
            final_cohp,
            icohp=final_icohp,
            are_coops=self.are_coops,
            are_cobis=self.are_cobis,
        )

    @classmethod
    def from_dict(cls, d):
        """Returns CompleteCohp object from dict representation."""
        cohp_dict = {}
        efermi = d["efermi"]
        energies = d["energies"]
        structure = Structure.from_dict(d["structure"])
        if "bonds" in d:
            bonds = {
                bond: {
                    "length": d["bonds"][bond]["length"],
                    "sites": tuple(PeriodicSite.from_dict(site) for site in d["bonds"][bond]["sites"]),
                }
                for bond in d["bonds"]
            }
        else:
            bonds = None
        for label in d["COHP"]:
            cohp = {Spin(int(spin)): np.array(d["COHP"][label][spin]) for spin in d["COHP"][label]}
            try:
                icohp = {Spin(int(spin)): np.array(d["ICOHP"][label][spin]) for spin in d["ICOHP"][label]}
            except KeyError:
                icohp = None
            if label == "average":
                avg_cohp = Cohp(efermi, energies, cohp, icohp=icohp)
            else:
                cohp_dict[label] = Cohp(efermi, energies, cohp, icohp=icohp)

        if "orb_res_cohp" in d:
            orb_cohp = {}
            for label in d["orb_res_cohp"]:
                orb_cohp[label] = {}
                for orb in d["orb_res_cohp"][label]:
                    cohp = {
                        Spin(int(s)): np.array(d["orb_res_cohp"][label][orb]["COHP"][s], dtype=float)
                        for s in d["orb_res_cohp"][label][orb]["COHP"]
                    }
                    try:
                        icohp = {
                            Spin(int(s)): np.array(d["orb_res_cohp"][label][orb]["ICOHP"][s], dtype=float)
                            for s in d["orb_res_cohp"][label][orb]["ICOHP"]
                        }
                    except KeyError:
                        icohp = None
                    orbitals = [(int(o[0]), Orbital[o[1]]) for o in d["orb_res_cohp"][label][orb]["orbitals"]]
                    orb_cohp[label][orb] = {
                        "COHP": cohp,
                        "ICOHP": icohp,
                        "orbitals": orbitals,
                    }
                # If no total COHPs are present, calculate the total
                # COHPs from the single-orbital populations. Total COHPs
                # may not be present when the cohpgenerator keyword is used
                # in LOBSTER versions 2.2.0 and earlier.
                if label not in d["COHP"] or d["COHP"][label] is None:
                    cohp = {
                        Spin.up: np.sum(
                            np.array([orb_cohp[label][orb]["COHP"][Spin.up] for orb in orb_cohp[label]]),
                            axis=0,
                        )
                    }
                    try:
                        cohp[Spin.down] = np.sum(
                            np.array([orb_cohp[label][orb]["COHP"][Spin.down] for orb in orb_cohp[label]]),
                            axis=0,
                        )
                    except KeyError:
                        pass

                orb_res_icohp = None in [orb_cohp[label][orb]["ICOHP"] for orb in orb_cohp[label]]
                if (label not in d["ICOHP"] or d["ICOHP"][label] is None) and orb_res_icohp:
                    icohp = {
                        Spin.up: np.sum(
                            np.array([orb_cohp[label][orb]["ICOHP"][Spin.up] for orb in orb_cohp[label]]),
                            axis=0,
                        )
                    }
                    try:
                        icohp[Spin.down] = np.sum(
                            np.array([orb_cohp[label][orb]["ICOHP"][Spin.down] for orb in orb_cohp[label]]),
                            axis=0,
                        )
                    except KeyError:
                        pass
        else:
            orb_cohp = None

        if "average" not in d["COHP"]:
            # calculate average
            cohp = np.array([np.array(c) for c in d["COHP"].values()]).mean(axis=0)
            try:
                icohp = np.array([np.array(c) for c in d["ICOHP"].values()]).mean(axis=0)
            except KeyError:
                icohp = None
            avg_cohp = Cohp(efermi, energies, cohp, icohp=icohp)

        are_cobis = d.get("are_cobis", False)

        return CompleteCohp(
            structure,
            avg_cohp,
            cohp_dict,
            bonds=bonds,
            are_coops=d["are_coops"],
            are_cobis=are_cobis,
            orb_res_cohp=orb_cohp,
        )

    @classmethod
    def from_file(cls, fmt, filename=None, structure_file=None, are_coops=False, are_cobis=False):
        """Creates a CompleteCohp object from an output file of a COHP
        calculation. Valid formats are either LMTO (for the Stuttgart
        LMTO-ASA code) or LOBSTER (for the LOBSTER code).

        Args:
            fmt: A string for the code that was used to calculate
                the COHPs so that the output file can be handled
                correctly. Can take the values "LMTO" or "LOBSTER".
            filename: Name of the COHP output file. Defaults to COPL
                for LMTO and COHPCAR.lobster/COOPCAR.lobster for LOBSTER.
            structure_file: Name of the file containing the structure.
                If no file name is given, use CTRL for LMTO and POSCAR
                for LOBSTER.
            are_coops: Indicates whether the populations are COOPs or
                COHPs. Defaults to False for COHPs.
            are_cobis: Indicates whether the populations are COBIs or
                COHPs. Defaults to False for COHPs.

        Returns:
            A CompleteCohp object.
        """
        if are_coops and are_cobis:
            raise ValueError("You cannot have info about COOPs and COBIs in the same file.")
        fmt = fmt.upper()
        if fmt == "LMTO":
            # LMTO COOPs and orbital-resolved COHP cannot be handled yet.
            are_coops = False
            are_cobis = False
            orb_res_cohp = None
            if structure_file is None:
                structure_file = "CTRL"
            if filename is None:
                filename = "COPL"
            cohp_file = LMTOCopl(filename=filename, to_eV=True)
        elif fmt == "LOBSTER":
            if are_coops and are_cobis:
                raise ValueError("You cannot have info about COOPs and COBIs in the same file.")
            if structure_file is None:
                structure_file = "POSCAR"
            if filename is None and filename is None:
                if are_coops:
                    filename = "COOPCAR.lobster"
                elif are_cobis:
                    filename = "COBICAR.lobster"
                else:
                    filename = "COHPCAR.lobster"
            cohp_file = Cohpcar(filename=filename, are_coops=are_coops, are_cobis=are_cobis)
            orb_res_cohp = cohp_file.orb_res_cohp
        else:
            raise ValueError(f"Unknown format {fmt}. Valid formats are LMTO and LOBSTER.")

        structure = Structure.from_file(structure_file)
        efermi = cohp_file.efermi
        cohp_data = cohp_file.cohp_data
        energies = cohp_file.energies

        # Lobster shifts the energies so that the Fermi energy is at zero.
        # Shifting should be done by the plotter object though.

        spins = [Spin.up, Spin.down] if cohp_file.is_spin_polarized else [Spin.up]
        if fmt == "LOBSTER":
            energies += efermi

        if orb_res_cohp is not None:
            # If no total COHPs are present, calculate the total
            # COHPs from the single-orbital populations. Total COHPs
            # may not be present when the cohpgenerator keyword is used
            # in LOBSTER versions 2.2.0 and earlier.
            # TODO: Test this more extensively

            for label in orb_res_cohp:
                if cohp_file.cohp_data[label]["COHP"] is None:
                    cohp_data[label]["COHP"] = {
                        sp: np.sum(
                            [orb_res_cohp[label][orbs]["COHP"][sp] for orbs in orb_res_cohp[label]],
                            axis=0,
                        )
                        for sp in spins
                    }
                if cohp_file.cohp_data[label]["ICOHP"] is None:
                    cohp_data[label]["ICOHP"] = {
                        sp: np.sum(
                            [orb_res_cohp[label][orbs]["ICOHP"][sp] for orbs in orb_res_cohp[label]],
                            axis=0,
                        )
                        for sp in spins
                    }

        if fmt == "LMTO":
            # Calculate the average COHP for the LMTO file to be
            # consistent with LOBSTER output.
            avg_data = {"COHP": {}, "ICOHP": {}}
            for i in avg_data:
                for spin in spins:
                    rows = np.array([v[i][spin] for v in cohp_data.values()])
                    avg = np.average(rows, axis=0)
                    # LMTO COHPs have 5 significant figures
                    avg_data[i].update({spin: np.array([round_to_sigfigs(a, 5) for a in avg], dtype=float)})
            avg_cohp = Cohp(efermi, energies, avg_data["COHP"], icohp=avg_data["ICOHP"])
        else:
            avg_cohp = Cohp(
                efermi,
                energies,
                cohp_data["average"]["COHP"],
                icohp=cohp_data["average"]["ICOHP"],
                are_coops=are_coops,
                are_cobis=are_cobis,
            )
            del cohp_data["average"]

        cohp_dict = {
            label: Cohp(efermi, energies, v["COHP"], icohp=v["ICOHP"], are_coops=are_coops, are_cobis=are_cobis)
            for label, v in cohp_data.items()
        }

        bond_dict = {
            label: {
                "length": v["length"],
                "sites": [structure[site] for site in v["sites"]],
            }
            for label, v in cohp_data.items()
        }

        return CompleteCohp(
            structure,
            avg_cohp,
            cohp_dict,
            bonds=bond_dict,
            are_coops=are_coops,
            are_cobis=are_cobis,
            orb_res_cohp=orb_res_cohp,
        )


class IcohpValue(MSONable):
    """Class to store information on an ICOHP or ICOOP value.

    Attributes:
        energies (ndarray): Energy values for the COHP/ICOHP/COOP/ICOOP.
        densities (ndarray): Density of states values for the COHP/ICOHP/COOP/ICOOP.
        energies_are_cartesian (bool): Whether the energies are cartesian or not.
        are_coops (bool): Whether the object is a COOP/ICOOP or not.
        are_cobis (bool): Whether the object is a COBIS/ICOBIS or not.
        icohp (dict): A dictionary of the ICOHP/COHP values. The keys are Spin.up and Spin.down.
        summed_icohp (float): The summed ICOHP/COHP values.
        num_bonds (int): The number of bonds used for the average COHP (relevant for Lobster versions <3.0).
    """

    def __init__(
        self, label, atom1, atom2, length, translation, num, icohp, are_coops=False, are_cobis=False, orbitals=None
    ) -> None:
        """
        Args:
            label: label for the icohp
            atom1: str of atom that is contributing to the bond
            atom2: str of second atom that is contributing to the bond
            length: float of bond lengths
            translation: translation list, e.g. [0,0,0]
            num: integer describing how often the bond exists
            icohp: dict={Spin.up: icohpvalue for spin.up, Spin.down: icohpvalue for spin.down}
            are_coops: if True, this are COOPs
            are_cobis: if True, this are COBIs
            orbitals: {[str(Orbital1)-str(Orbital2)]: {"icohp":{Spin.up: icohpvalue for spin.up, Spin.down:
                icohpvalue for spin.down}, "orbitals":[Orbital1, Orbital2]}}.

        """
        if are_coops and are_cobis:
            raise ValueError("You cannot have info about COOPs and COBIs in the same file.")
        self._are_coops = are_coops
        self._are_cobis = are_cobis
        self._label = label
        self._atom1 = atom1
        self._atom2 = atom2
        self._length = length
        self._translation = translation
        self._num = num
        self._icohp = icohp
        self._orbitals = orbitals
        if Spin.down in self._icohp:
            self._is_spin_polarized = True
        else:
            self._is_spin_polarized = False

    def __str__(self) -> str:
        """String representation of the ICOHP/ICOOP."""
        if not self._are_coops and not self._are_cobis:
            if self._is_spin_polarized:
                return (
                    f"ICOHP {self._label} between {self._atom1} and {self._atom2} ({self._translation}): "
                    f"{self._icohp[Spin.up]} eV (Spin up) and {self._icohp[Spin.down]} eV (Spin down)"
                )
            return (
                f"ICOHP {self._label} between {self._atom1} and {self._atom2} ({self._translation}): "
                f"{self._icohp[Spin.up]} eV (Spin up)"
            )
        if self._are_coops and not self._are_cobis:
            if self._is_spin_polarized:
                return (
                    f"ICOOP {self._label} between {self._atom1} and {self._atom2} ({self._translation}): "
                    f"{self._icohp[Spin.up]} eV (Spin up) and {self._icohp[Spin.down]} eV (Spin down)"
                )
            return (
                f"ICOOP {self._label} between {self._atom1} and {self._atom2} ({self._translation}): "
                f"{self._icohp[Spin.up]} eV (Spin up)"
            )
        if self._are_cobis and not self._are_coops:
            if self._is_spin_polarized:
                return (
                    f"ICOBI {self._label} between {self._atom1} and {self._atom2} ({self._translation}): "
                    f"{self._icohp[Spin.up]} eV (Spin up) and {self._icohp[Spin.down]} eV (Spin down)"
                )
            return (
                f"ICOBI {self._label} between {self._atom1} and {self._atom2} ({self._translation}): "
                f"{self._icohp[Spin.up]} eV (Spin up)"
            )

        return ""

    @property
    def num_bonds(self):
        """Tells the number of bonds for which the ICOHP value is an average.

        Returns:
            Int.
        """
        return self._num

    @property
    def are_coops(self) -> bool:
        """Tells if ICOOPs or not.

        Returns:
            Boolean.
        """
        return self._are_coops

    @property
    def are_cobis(self) -> bool:
        """Tells if ICOBIs or not.

        Returns:
            Boolean.
        """
        return self._are_cobis

    @property
    def is_spin_polarized(self) -> bool:
        """Tells if spin polarized calculation or not.

        Returns:
            Boolean.
        """
        return self._is_spin_polarized

    def icohpvalue(self, spin=Spin.up):
        """
        Args:
            spin: Spin.up or Spin.down.

        Returns:
            icohpvalue (float) corresponding to chosen spin.
        """
        if not self.is_spin_polarized and spin == Spin.down:
            raise ValueError("The calculation was not performed with spin polarization")

        return self._icohp[spin]

    def icohpvalue_orbital(self, orbitals, spin=Spin.up):
        """
        Args:
            orbitals: List of Orbitals or "str(Orbital1)-str(Orbital2)"
            spin: Spin.up or Spin.down.

        Returns:
            icohpvalue (float) corresponding to chosen spin.
        """
        if not self.is_spin_polarized and spin == Spin.down:
            raise ValueError("The calculation was not performed with spin polarization")
        if isinstance(orbitals, list):
            orbitals = f"{orbitals[0]}-{orbitals[1]}"
        return self._orbitals[orbitals]["icohp"][spin]

    @property
    def icohp(self):
        """Dict with icohps for spinup and spindown
        Return:
            dict={Spin.up: icohpvalue for spin.up, Spin.down: icohpvalue for spin.down}.
        """
        return self._icohp

    @property
    def summed_icohp(self):
        """Sums ICOHPs of both spin channels for spin polarized compounds.

        Returns:
            float: icohp value in eV.
        """
        return self._icohp[Spin.down] + self._icohp[Spin.up] if self._is_spin_polarized else self._icohp[Spin.up]

    @property
    def summed_orbital_icohp(self):
        """Sums orbitals-resolved ICOHPs of both spin channels for spin-plarized compounds.

        Returns:
            {"str(Orbital1)-str(Ortibal2)": icohp value in eV}.

        """
        orbital_icohp = {}
        for orb, item in self._orbitals.items():
            orbital_icohp[orb] = (
                item["icohp"][Spin.up] + item["icohp"][Spin.down] if self._is_spin_polarized else item["icohp"][Spin.up]
            )
        return orbital_icohp


class IcohpCollection(MSONable):
    """Class to store IcohpValues.

    Attributes:
        are_coops (bool): Boolean to indicate if these are ICOOPs.
        are_cobis (bool): Boolean to indicate if these are ICOOPs.
        is_spin_polarized (bool): Boolean to indicate if the Lobster calculation was done spin polarized or not.
    """

    def __init__(
        self,
        list_labels,
        list_atom1,
        list_atom2,
        list_length,
        list_translation,
        list_num,
        list_icohp,
        is_spin_polarized,
        list_orb_icohp=None,
        are_coops=False,
        are_cobis=False,
    ) -> None:
        """
        Args:
            list_labels: list of labels for ICOHP/ICOOP values
            list_atom1: list of str of atomnames e.g. "O1"
            list_atom2: list of str of atomnames e.g. "O1"
            list_length: list of lengths of corresponding bonds in Angstrom
            list_translation: list of translation list, e.g. [0,0,0]
            list_num: list of equivalent bonds, usually 1 starting from Lobster 3.0.0
            list_icohp: list of dict={Spin.up: icohpvalue for spin.up, Spin.down: icohpvalue for spin.down}
            is_spin_polarized: Boolean to indicate if the Lobster calculation was done spin polarized or not Boolean to
                indicate if the Lobster calculation was done spin polarized or not
            list_orb_icohp: list of dict={[str(Orbital1)-str(Orbital2)]: {"icohp":{Spin.up: icohpvalue for spin.up,
                Spin.down: icohpvalue for spin.down}, "orbitals":[Orbital1, Orbital2]}}
            are_coops: Boolean to indicate whether ICOOPs are stored
            are_cobis: Boolean to indicate whether ICOBIs are stored.
        """
        if are_coops and are_cobis:
            raise ValueError("You cannot have info about COOPs and COBIs in the same file.")
        self._are_coops = are_coops
        self._are_cobis = are_cobis
        self._icohplist = {}
        self._is_spin_polarized = is_spin_polarized
        self._list_labels = list_labels
        self._list_atom1 = list_atom1
        self._list_atom2 = list_atom2
        self._list_length = list_length
        self._list_translation = list_translation
        self._list_num = list_num
        self._list_icohp = list_icohp
        self._list_orb_icohp = list_orb_icohp

        for ilist, listel in enumerate(list_labels):
            self._icohplist[listel] = IcohpValue(
                label=listel,
                atom1=list_atom1[ilist],
                atom2=list_atom2[ilist],
                length=list_length[ilist],
                translation=list_translation[ilist],
                num=list_num[ilist],
                icohp=list_icohp[ilist],
                are_coops=are_coops,
                are_cobis=are_cobis,
                orbitals=None if list_orb_icohp is None else list_orb_icohp[ilist],
            )

    def __str__(self) -> str:
        joinstr = []
        for value in self._icohplist.values():
            joinstr.append(str(value))
        return "\n".join(joinstr)

    def get_icohp_by_label(self, label, summed_spin_channels=True, spin=Spin.up, orbitals=None):
        """Get an icohp value for a certain bond as indicated by the label (bond labels starting by "1" as in
        ICOHPLIST/ICOOPLIST).

        Args:
            label: label in str format (usually the bond number in Icohplist.lobster/Icooplist.lobster
            summed_spin_channels: Boolean to indicate whether the ICOHPs/ICOOPs of both spin channels should be summed
            spin: if summed_spin_channels is equal to False, this spin indicates which spin channel should be returned
            orbitals: List of Orbital or "str(Orbital1)-str(Orbital2)"

        Returns:
            float describing ICOHP/ICOOP value
        """
        icohp_here = self._icohplist[label]
        if orbitals is None:
            if summed_spin_channels:
                return icohp_here.summed_icohp
            return icohp_here.icohpvalue(spin)

        if isinstance(orbitals, list):
            orbitals = f"{orbitals[0]}-{orbitals[1]}"
        if summed_spin_channels:
            return icohp_here.summed_orbital_icohp[orbitals]

        return icohp_here.icohpvalue_orbital(spin=spin, orbitals=orbitals)

    def get_summed_icohp_by_label_list(self, label_list, divisor=1.0, summed_spin_channels=True, spin=Spin.up):
        """Get the sum of several ICOHP values that are indicated by a list of labels
        (labels of the bonds are the same as in ICOHPLIST/ICOOPLIST).

        Args:
            label_list: list of labels of the ICOHPs/ICOOPs that should be summed
            divisor: is used to divide the sum
            summed_spin_channels: Boolean to indicate whether the ICOHPs/ICOOPs of both spin channels should be summed
            spin: if summed_spin_channels is equal to False, this spin indicates which spin channel should be returned

        Returns:
            float that is a sum of all ICOHPs/ICOOPs as indicated with label_list
        """
        sum_icohp = 0
        for label in label_list:
            icohp_here = self._icohplist[label]
            if icohp_here.num_bonds != 1:
                warnings.warn("One of the ICOHP values is an average over bonds. This is currently not considered.")
            if icohp_here._is_spin_polarized:
                if summed_spin_channels:
                    sum_icohp = sum_icohp + icohp_here.summed_icohp
                else:
                    sum_icohp = sum_icohp + icohp_here.icohpvalue(spin)
            else:
                sum_icohp = sum_icohp + icohp_here.icohpvalue(spin)
        return sum_icohp / divisor

    def get_icohp_dict_by_bondlengths(self, minbondlength=0.0, maxbondlength=8.0):
        """Get a dict of IcohpValues corresponding to certain bond lengths.

        Args:
            minbondlength: defines the minimum of the bond lengths of the bonds
            maxbondlength: defines the maximum of the bond lengths of the bonds.

        Returns:
            dict of IcohpValues, the keys correspond to the values from the initial list_labels.
        """
        new_icohp_dict = {}
        for value in self._icohplist.values():
            if value._length >= minbondlength and value._length <= maxbondlength:
                new_icohp_dict[value._label] = value
        return new_icohp_dict

    def get_icohp_dict_of_site(
        self,
        site,
        minsummedicohp=None,
        maxsummedicohp=None,
        minbondlength=0.0,
        maxbondlength=8.0,
        only_bonds_to=None,
    ):
        """Get a dict of IcohpValue for a certain site (indicated by integer).

        Args:
            site: integer describing the site of interest, order as in Icohplist.lobster/Icooplist.lobster, starts at 0
            minsummedicohp: float, minimal icohp/icoop of the bonds that are considered. It is the summed ICOHP value
                from both spin channels for spin polarized cases
            maxsummedicohp: float, maximal icohp/icoop of the bonds that are considered. It is the summed ICOHP value
                from both spin channels for spin polarized cases
            minbondlength: float, defines the minimum of the bond lengths of the bonds
            maxbondlength: float, defines the maximum of the bond lengths of the bonds
            only_bonds_to: list of strings describing the bonding partners that are allowed, e.g. ['O']

        Returns:
            dict of IcohpValues, the keys correspond to the values from the initial list_labels
        """
        new_icohp_dict = {}
        for key, value in self._icohplist.items():
            atomnumber1 = int(re.split(r"(\d+)", value._atom1)[1]) - 1
            atomnumber2 = int(re.split(r"(\d+)", value._atom2)[1]) - 1
            if site in (atomnumber1, atomnumber2):
                # manipulate order of atoms so that searched one is always atom1
                if site == atomnumber2:
                    save = value._atom1
                    value._atom1 = value._atom2
                    value._atom2 = save

                second_test = True if only_bonds_to is None else re.split("(\\d+)", value._atom2)[0] in only_bonds_to
                if value._length >= minbondlength and value._length <= maxbondlength and second_test:
                    if minsummedicohp is not None:
                        if value.summed_icohp >= minsummedicohp:
                            if maxsummedicohp is not None:
                                if value.summed_icohp <= maxsummedicohp:
                                    new_icohp_dict[key] = value
                            else:
                                new_icohp_dict[key] = value
                    elif maxsummedicohp is not None:
                        if value.summed_icohp <= maxsummedicohp:
                            new_icohp_dict[key] = value
                    else:
                        new_icohp_dict[key] = value

        return new_icohp_dict

    def extremum_icohpvalue(self, summed_spin_channels=True, spin=Spin.up):
        """Get ICOHP/ICOOP of strongest bond.

        Args:
            summed_spin_channels: Boolean to indicate whether the ICOHPs/ICOOPs of both spin channels should be summed.

            spin: if summed_spin_channels is equal to False, this spin indicates which spin channel should be returned

        Returns:
            lowest ICOHP/largest ICOOP value (i.e. ICOHP/ICOOP value of strongest bond)
        """
        extremum = -sys.float_info.max if self._are_coops or self._are_cobis else sys.float_info.max

        if not self._is_spin_polarized:
            if spin == Spin.down:
                warnings.warn("This spin channel does not exist. I am switching to Spin.up")
            spin = Spin.up

        for value in self._icohplist.values():
            if not value.is_spin_polarized or not summed_spin_channels:
                if not self._are_coops and not self._are_cobis:
                    if value.icohpvalue(spin) < extremum:
                        extremum = value.icohpvalue(spin)
                elif value.icohpvalue(spin) > extremum:
                    extremum = value.icohpvalue(spin)
            elif not self._are_coops and not self._are_cobis:
                if value.summed_icohp < extremum:
                    extremum = value.summed_icohp
            elif value.summed_icohp > extremum:
                extremum = value.summed_icohp
        return extremum

    @property
    def is_spin_polarized(self) -> bool:
        """Whether it is spin polarized."""
        return self._is_spin_polarized

    @property
    def are_coops(self) -> bool:
        """Whether this is a coop."""
        return self._are_coops

    @property
    def are_cobis(self) -> bool:
        """Whether this a cobi."""
        return self._are_cobis


def get_integrated_cohp_in_energy_range(
    cohp, label, orbital=None, energy_range=None, relative_E_Fermi=True, summed_spin_channels=True
):
    """Method that can integrate completecohp objects which include data on integrated COHPs
    Args:
        cohp: CompleteCOHP object
        label: label of the COHP data
        orbital: If not None, a orbital resolved integrated COHP will be returned
        energy_range:   if None, returns icohp value at Fermi level;
                        if float, integrates from this float up to the Fermi level;
                        if [float,float], will integrate in between
        relative_E_Fermi: if True, energy scale with E_Fermi at 0 eV is chosen
        summed_spin_channels: if True, Spin channels will be summed.

    Returns:
        float indicating the integrated COHP if summed_spin_channels==True, otherwise dict of the following form {
        Spin.up:float, Spin.down:float}
    """
    summedicohp = {}
    if orbital is None:
        icohps = cohp.all_cohps[label].get_icohp(spin=None)
        if summed_spin_channels and Spin.down in icohps:
            summedicohp[Spin.up] = icohps[Spin.up] + icohps[Spin.down]
        else:
            summedicohp = icohps
    else:
        icohps = cohp.get_orbital_resolved_cohp(label=label, orbitals=orbital).icohp
        if summed_spin_channels and Spin.down in icohps:
            summedicohp[Spin.up] = icohps[Spin.up] + icohps[Spin.down]
        else:
            summedicohp = icohps

    if energy_range is None:
        energies_corrected = cohp.energies - cohp.efermi
        spl_spinup = InterpolatedUnivariateSpline(energies_corrected, summedicohp[Spin.up], ext=0)

        if not summed_spin_channels and Spin.down in icohps:
            spl_spindown = InterpolatedUnivariateSpline(energies_corrected, summedicohp[Spin.down], ext=0)
            return {Spin.up: spl_spinup(0.0), Spin.down: spl_spindown(0.0)}
        if summed_spin_channels:
            return spl_spinup(0.0)

        return {Spin.up: spl_spinup(0.0)}

    # returns icohp value at the Fermi level!
    if isinstance(energy_range, float):
        if relative_E_Fermi:
            energies_corrected = cohp.energies - cohp.efermi
            spl_spinup = InterpolatedUnivariateSpline(energies_corrected, summedicohp[Spin.up], ext=0)

            if not summed_spin_channels and Spin.down in icohps:
                spl_spindown = InterpolatedUnivariateSpline(energies_corrected, summedicohp[Spin.down], ext=0)
                return {
                    Spin.up: spl_spinup(0) - spl_spinup(energy_range),
                    Spin.down: spl_spindown(0) - spl_spindown(energy_range),
                }
            if summed_spin_channels:
                return spl_spinup(0) - spl_spinup(energy_range)
            return {Spin.up: spl_spinup(0) - spl_spinup(energy_range)}

        energies_corrected = cohp.energies
        spl_spinup = InterpolatedUnivariateSpline(energies_corrected, summedicohp[Spin.up], ext=0)

        if not summed_spin_channels and Spin.down in icohps:
            spl_spindown = InterpolatedUnivariateSpline(energies_corrected, summedicohp[Spin.down], ext=0)
            return {
                Spin.up: spl_spinup(cohp.efermi) - spl_spinup(energy_range),
                Spin.down: spl_spindown(cohp.efermi) - spl_spindown(energy_range),
            }
        if summed_spin_channels:
            return spl_spinup(cohp.efermi) - spl_spinup(energy_range)
        return {Spin.up: spl_spinup(cohp.efermi) - spl_spinup(energy_range)}

    energies_corrected = cohp.energies - cohp.efermi if relative_E_Fermi else cohp.energies

    spl_spinup = InterpolatedUnivariateSpline(energies_corrected, summedicohp[Spin.up], ext=0)

    if not summed_spin_channels and Spin.down in icohps:
        spl_spindown = InterpolatedUnivariateSpline(energies_corrected, summedicohp[Spin.down], ext=0)
        return {
            Spin.up: spl_spinup(energy_range[1]) - spl_spinup(energy_range[0]),
            Spin.down: spl_spindown(energy_range[1]) - spl_spindown(energy_range[0]),
        }
    if summed_spin_channels:
        return spl_spinup(energy_range[1]) - spl_spinup(energy_range[0])

    return {Spin.up: spl_spinup(energy_range[1]) - spl_spinup(energy_range[0])}
