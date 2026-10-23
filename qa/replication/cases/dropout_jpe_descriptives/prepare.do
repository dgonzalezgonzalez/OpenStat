* Prepare step for the Dropout-JPE replication case.
*
* The package ships the full analysis dataset as a 2 GB compressed archive
* (data/nlsy97/cleaned/y97_all_tscrGPA.dta.zip) with 170,696 observations
* and 7,605 variables.  That is far more than a conformance run needs, and
* the descriptive statistics do not use most of it.
*
* This script reduces the archive to the columns that the package's own
* descriptives code touches, so the case can run in seconds.  The
* reduction happens in Stata so that the data keeps Stata's own types,
* missing values and value labels.

clear all
set more off
set maxvar 30000

local pkg "${pkg_root}"
if "`pkg'" == "" local pkg "../_packages/Dropout-JPE"

* Unpack with Stata's own unzipfile so that no external tool is required
* (Windows hosts have no `unzip`).  The current directory has to come back
* to the work directory afterwards, because the reduced dataset is saved
* next to the case, not inside the package.
local work "`c(pwd)'"
capture confirm file "`pkg'/data/nlsy97/cleaned/y97_all_tscrGPA.dta"
if _rc {
    cd "`pkg'/data/nlsy97/cleaned"
    unzipfile "y97_all_tscrGPA.dta.zip", replace
    cd "`work'"
}

use "`pkg'/data/nlsy97/cleaned/y97_all_tscrGPA.dta", clear

keep ID year male anyFlag choice25 choice15 log_comp weeks_worked_Oct ///
     avgHrsOct GPA cum_2yr cum_college cum_grad_school male Peduc Grades_HS_best

compress
save "small.dta", replace
describe
count
