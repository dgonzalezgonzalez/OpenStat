* Replication case: BDMMM random data generation.
*
* Source: Andrew Bernard, Andreas Blume, David Dollar, Mikkel Ejegod and
* Jesper Johannes, "The Origins of Firm Heterogeneity: A Production
* Network Approach", Journal of Political Economy.  The package ships
* MATLAB for the structural estimation and Stata for the data
* construction; task0_randomdata is the Stata half that fabricates a
* stand-in dataset so the whole pipeline can run without the restricted
* administrative data.
*
* The code below is the package's own task0, with the two sizes scaled
* down (the package uses 500,000 links and 50,000 firms per year) so a
* conformance run finishes in seconds.  Every Stata construct is the
* package's: forvalues over a global range, set seed, runiform and
* rnormal, duplicates drop with force, tostring with replace, append
* using and foreach over a list of variable names.

clear all
set more off

global nlinks 20000
global nfirms 4000
global start 2002
global end 2005
global first 2003

set seed 871835

capture mkdir tmp
capture mkdir output

*-----------
* 1. network
*-----------
forvalues t = $start/$end {
	clear
	set obs $nlinks
	gen year = `t'
	gen vat_i = floor(($nfirms - 1)*runiform() + 1) 		// random links
	gen vat_j = floor(($nfirms - 1)*runiform() + 1)
	drop if vat_i==vat_j 									// no self-loops
	duplicates drop vat_i vat_j, force 						// no multiple edges
	gen sales_ij = 250+ exp(rnormal()*5 + 5)  				// lognormal() link values
	save "tmp/network_`t'", replace
}

use "tmp/network_$start", clear
	forvalues t = $first/$end {
		append using  "tmp/network_`t'"
	}
save  "output/Belgian B2B flows_large_cleaned_n_balanced", replace

*--------------
* 2. NACE codes
*--------------
// NACE source 1
forvalues t = $start/$end {
	clear
	set obs $nfirms
	gen year = `t'
	gen vat = _n											// balanced panel (for unbalanced draw unif)
	gen cnace = 100 + int((9600)*runiform()) 				// 4-digit NACE
	replace cnace = floor(cnace/10)*10                      // scale back on # sectors to have enough firms within each sector
	tostring cnace, replace
	save "tmp/nace_`t'", replace
}

use "tmp/nace_$start", clear
	forvalues t = $first/$end {
		append using  "tmp/nace_`t'"
	}
save  "output/NACE_0214", replace

// NACE source 2 + zip codes
forvalues t = $start/$end {
	clear
	set obs $nfirms
	gen year = `t'
	gen vat = _n											// balanced panel (for unbalanced draw unif)

	gen C_NACE = 100 + int((9600)*runiform()) 				// 4-digit NACE
	replace C_NACE = floor(C_NACE/10)*10            		// scale back on # sectors to have enough firms within each sector
	tostring C_NACE, replace
	gen POST_CODE = 100*(10 + int((99-10+1)*runiform())) 			// zip code
	save "tmp/nacebel_`t'", replace
}

use "tmp/nacebel_$start", clear
	forvalues t = $first/$end {
		append using  "tmp/nacebel_`t'"
	}
save  "output/NBB_B2B_sample_large_nace_n_zip_codes", replace

*--------------------------------------
* 3. annual accounts + VAT declarations
*--------------------------------------
forvalues t = $start/$end {
	clear
	set obs $nfirms
	gen year = `t'
	gen vat = _n											// balanced panel (for unbalanced draw unif)
	foreach x in turnover total_inputs laborcost {			// firm chars
		gen `x' = floor(exp(rnormal()*5) + 1) + 1000
	}
	foreach x in empl {
		gen `x' = floor(exp(rnormal()*2) + 1)
	}
	save "tmp/firms_`t'", replace
}

use "tmp/firms_$start", clear
	forvalues t = $first/$end {
		append using  "tmp/firms_`t'"
	}
save  "output/annualized_annac_02_14", replace

*=================================================
* Report the shape of what was produced
*=================================================
use "output/annualized_annac_02_14", clear
count
display "__OPENSTAT__n_firms=" %21.14g r(N)
summarize turnover empl
display "__OPENSTAT__turnover_mean=" %21.14g r(mean)
display "__OPENSTAT__turnover_sd=" %21.14g r(sd)
display "__OPENSTAT__empl_mean=" %21.14g r(mean)
display "__OPENSTAT__empl_sd=" %21.14g r(sd)

use "output/Belgian B2B flows_large_cleaned_n_balanced", clear
count
display "__OPENSTAT__n_links=" %21.14g r(N)
summarize sales_ij
display "__OPENSTAT__sales_mean=" %21.14g r(mean)
display "__OPENSTAT__sales_sd=" %21.14g r(sd)
count if vat_i==vat_j
display "__OPENSTAT__n_selfloops=" %21.14g r(N)
duplicates report vat_i vat_j
