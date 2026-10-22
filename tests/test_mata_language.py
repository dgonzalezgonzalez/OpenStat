"""Mata source-language behavior from public grammar and synthetic probes."""
import numpy as np
import pytest

from openstat import Engine
from openstat.mata import MataError


@pytest.fixture
def mata(tmp_path):
    return Engine(tmp_path).mata


def test_typed_function_for_if_else_and_return_native_verified(mata):
    mata.run(r"""
real scalar function weighted(real vector x, real scalar p) {
    real scalar i, result
    result=0
    for (i=1; i<=length(x); i++) {
        if (x[i]>2) result=result+x[i]^p
        else result=result+1
    }
    return(result)
}
answer=weighted(1..4,2)
""")
    assert mata.variables["answer"] == 27
    assert "i" not in mata.variables
    assert "result" not in mata.variables


def test_by_reference_scalar_matrix_and_local_copy_native_verified(mata):
    mata.run(r"""
void function change(real matrix A, real scalar n) {
    A[1,1]=9
    n=n+2
}
function copied(A) {
    B=A
    B[1,1]=100
    return(B)
}
A=(1,2\3,4)
n=5
change(A,n)
C=copied(A)
""")
    assert mata.variables["n"] == 7
    assert mata.variables["A"][0, 0] == 9
    assert mata.variables["C"][0, 0] == 100


def test_optional_arguments_and_args(mata):
    mata.run(r"""
real scalar function add(real scalar x, | real scalar y) {
    if (args()==1) y=10
    return(x+y)
}
a=add(5)
b=add(5,2)
""")
    assert mata.variables["a"] == 15
    assert mata.variables["b"] == 7


def test_recursive_function_and_private_variables(mata):
    mata.run(r"""
real scalar factorial(real scalar n) {
    if (n<=1) return(1)
    return(n*factorial(n-1))
}
n=99
result=factorial(6)
""")
    assert mata.variables["result"] == 720
    assert mata.variables["n"] == 99


def test_while_do_break_continue_and_increment(mata):
    mata.run(r"""
i=0; total=0
while (i<10) {
    ++i
    if (i==2) continue
    if (i==5) break
    total=total+i
}
do { total=total-1 } while (total>5)
old=i++
new=--i
""")
    assert mata.variables["total"] == 5
    assert mata.variables["old"] == 5
    assert mata.variables["new"] == 5


def test_function_scope_and_external_globals(mata):
    mata.run(r"""
global_value=1
void function update() {
    external real scalar global_value
    real scalar local_value
    local_value=50
    { global_value=global_value+local_value }
}
update()
""")
    assert mata.variables["global_value"] == 51
    assert "local_value" not in mata.variables


@pytest.mark.parametrize("code", [
    'real scalar top_level',
    'real scalar function f(real scalar x) { return((1,2)) }\na=f(1)',
    'function f(real rowvector x) { return(x) }\na=f(1::2)',
    'function f() { real scalar x\nx="bad" }\nf()',
    'function f() { real matrix x\nx="bad" }\nf()',
    'function f(x) { return(x) }\nf()',
    'void function f() { return(1) }\nf()',
])
def test_declaration_function_type_and_argument_errors(mata, code):
    with pytest.raises(MataError):
        mata.run(code)


def test_declared_empty_shapes(mata):
    mata.run(r"""
function empty() {
    real scalar s
    real rowvector r
    real colvector c
    real matrix m
    return((rows(r),cols(r),rows(c),cols(c),rows(m),cols(m),s==.))
}
result=empty()
""")
    np.testing.assert_array_equal(mata.variables["result"], [[1, 0, 0, 1, 0, 0, 1]])


@pytest.mark.parametrize("expression, expected", [
    ("(1,2\\3,4):*(10,20)", [[10, 40], [30, 80]]),
    ("(1,2\\3,4):+(10\\20)", [[11, 12], [23, 24]]),
    ("2:^(1,2)", [[2, 4]]),
    ("(1,4):/(2,2)", [[.5, 2]]),
    ("(1,2):==(1,3)", [[1, 0]]),
    ("(1,2):>(0,3)", [[1, 0]]),
    ("(0,1):&(1,1)", [[0, 1]]),
    ("(0,1):|(0,0)", [[0, 1]]),
    ("1.5..4.4", [[1.5, 2.5, 3.5]]),
    ("4::1", [[4], [3], [2], [1]]),
    ("(1+2,4*2\\6-1,3^2)", [[3, 8], [5, 9]]),
])
def test_colon_operators_ranges_and_expression_literals(mata, expression, expected):
    np.testing.assert_allclose(mata.evaluate(expression), expected)


@pytest.mark.parametrize("expression", ["(1,2):*(1\\2)", "(1,2)+(3\\4)", "(1,2)/I(2)", "I(2)^2", "(1,2\\3,4)[1]"])
def test_invalid_conformability_still_fails(mata, expression):
    with pytest.raises(MataError):
        mata.evaluate(expression)


def test_join_matrix_expressions_and_general_transpose(mata):
    mata.run("A=(1,2\\3,4)\nB=(A,J(2,1,1))\nC=(A\\I(2))\nD=(A+I(2))'\nE=A'A")
    np.testing.assert_array_equal(mata.variables["B"], [[1, 2, 1], [3, 4, 1]])
    assert mata.variables["C"].shape == (4, 2)
    np.testing.assert_array_equal(mata.variables["D"], [[2, 3], [2, 5]])
    np.testing.assert_array_equal(mata.variables["E"], [[10, 14], [14, 20]])


def test_list_range_and_vector_subscripts_with_assignment(mata):
    mata.run(r"""
A=(1,2,3\4,5,6\7,8,9)
B=A[(3\1),(3,1)]
C=A[|1,2\2,3|]
D=A[,2]
A[(1,3),2]=(20\80)
v=1..5
q=v[(5\2)]
v[|2\4|]=(9,8,7)
""")
    np.testing.assert_array_equal(mata.variables["B"], [[9, 7], [3, 1]])
    np.testing.assert_array_equal(mata.variables["C"], [[2, 3], [5, 6]])
    np.testing.assert_array_equal(mata.variables["D"], [[2], [5], [8]])
    np.testing.assert_array_equal(mata.variables["A"][:, 1], [20, 5, 80])
    np.testing.assert_array_equal(mata.variables["q"], [[5, 2]])
    np.testing.assert_array_equal(mata.variables["v"], [[1, 9, 8, 7, 5]])


def test_range_subscript_missing_endpoint(mata):
    mata.run("A=(1,2,3\\4,5,6)\nB=A[|1,2\\.,.|]")
    np.testing.assert_array_equal(mata.variables["B"], [[2, 3], [5, 6]])


@pytest.mark.parametrize("expression", ["(1,2)[0]", "(1,2)[1.5]", "(1,2)[3]", "I(2)[(1,2\\1,2),1]", "I(2)[|2,1\\1,2|]"])
def test_invalid_subscripts(mata, expression):
    with pytest.raises(MataError):
        mata.evaluate(expression)


def test_selection_reshape_and_vectorization(mata):
    mata.run(r"""
A=(1,2\3,4\5,6)
B=select(A,(1\0\1))
C=select(A,(0,1))
D=selectindex((0,1,0,1))
E=rowshape(A,2)
F=colshape(A,3)
G=vec(A)
""")
    np.testing.assert_array_equal(mata.variables["B"], [[1, 2], [5, 6]])
    np.testing.assert_array_equal(mata.variables["C"], [[2], [4], [6]])
    np.testing.assert_array_equal(mata.variables["D"], [[2, 4]])
    np.testing.assert_array_equal(mata.variables["E"], [[1, 2, 3], [4, 5, 6]])
    np.testing.assert_array_equal(mata.variables["F"], [[1, 2, 3], [4, 5, 6]])
    np.testing.assert_array_equal(mata.variables["G"], [[1], [3], [5], [2], [4], [6]])


def test_missing_counts_sums_and_casewise_moments(mata):
    mata.run(r"""
A=(1,2\.,3\5,6)
missing_count=missing(A)
s=sum(A)
sm=sum(A,1)
rowcounts=rowmissing(A)
means=mean(A)
covariance=variance(A)
weighted=mean(A,(1\100\3))
""")
    assert mata.variables["missing_count"] == 1
    assert mata.variables["s"] == 17
    assert np.isnan(mata.variables["sm"])
    np.testing.assert_array_equal(mata.variables["rowcounts"], [[0], [1], [0]])
    np.testing.assert_allclose(mata.variables["means"], [[3, 4]])
    np.testing.assert_allclose(mata.variables["covariance"], [[8, 8], [8, 8]])
    np.testing.assert_allclose(mata.variables["weighted"], [[4, 5]])


def test_cross_products_omit_missing_and_support_weights_and_constants(mata):
    mata.run(r"""
X=(1\2\.)
y=(3\4\5)
xy=cross(X,y)
weighted=cross(X,(2\3\4),y)
constants=cross(X,1,y,1)
""")
    np.testing.assert_array_equal(mata.variables["xy"], [[11]])
    np.testing.assert_array_equal(mata.variables["weighted"], [[30]])
    np.testing.assert_array_equal(mata.variables["constants"], [[11, 3], [7, 2]])


def test_st_data_store_matrix_scalar_and_macros(mata):
    engine = mata.engine
    assert engine.run("set obs 3\ngen x=_n\ngen y=0").rc == 0
    mata.run('X=st_data((1\\3),"x")\nst_store((1\\3),"y",X:*2)\nst_matrix("M",X)\nst_numscalar("S",sum(X))\nst_local("hello","world")')
    np.testing.assert_array_equal(engine.data.y, [2, 0, 6])
    np.testing.assert_array_equal(engine.matrices["M"], [[1], [3]])
    assert engine.scalars["S"] == 4
    assert engine.locals["hello"] == "world"


def test_string_literals_have_no_backslash_escapes(mata):
    assert mata.evaluate('"a\\nb"') == "a\\nb"
    mata.run('string scalar function join(string scalar s) { return(s+"!") }\nresult=join("hello")')
    assert mata.variables["result"] == "hello!"


def test_string_matrix_assignment_preserves_full_string_width(mata):
    mata.run('A=J(1,2,"")\nA[1,1]="long text"')
    assert mata.variables["A"][0, 0] == "long text"


def test_assignment_argument_remains_a_reference(mata):
    mata.run('void function update(real scalar x) { x=9 }\nb=1\nupdate(b=2)')
    assert mata.variables["b"] == 9


def test_mata_calls_link_before_conditional_evaluation(mata):
    assert mata.evaluate("1 ? 5 : ln(-1)") == 5
    with pytest.raises(MataError, match="absentfunction"):
        mata.evaluate("1 ? 5 : absentfunction()")


def test_matrix_allocation_limit_checked_before_allocation(mata):
    mata.max_cells = 100
    with pytest.raises(MataError, match="allocation limit"):
        mata.evaluate("J(1000,1000,0)")
    with pytest.raises(MataError, match="allocation limit"):
        mata.evaluate("I(1000)")


def test_function_and_loop_source_through_do_file_engine(mata):
    result = mata.engine.run("""mata:
real scalar squaretotal(real vector x) {
real scalar i,s
s=0
for (i=1;i<=length(x);i++) {
s=s+x[i]^2
}
return(s)
}
st_numscalar("result",squaretotal(1..4))
end
""")
    assert result.rc == 0, result.output
    assert mata.engine.scalars["result"] == 30


def test_step_limit_stops_infinite_loop(mata):
    mata.max_steps = 1000
    with pytest.raises(MataError, match="step limit"):
        mata.run("i=0\nwhile(1) i++")


def test_recursive_call_limit_and_no_python_execution(mata):
    mata.run("function recurse(x) { return(recurse(x)) }")
    with pytest.raises(MataError, match="recursion limit"):
        mata.evaluate("recurse(1)")
    with pytest.raises(MataError):
        mata.evaluate('__import__("os").system("echo never")')


def test_unsupported_compiled_loading_remains_explicit(mata):
    with pytest.raises(MataError):
        mata.run("mata mlib index")
