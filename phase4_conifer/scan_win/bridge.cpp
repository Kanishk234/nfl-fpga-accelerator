#include "conifer.h"
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include "ap_fixed.h"
struct BDTConfig : conifer::ConiferConfiguration {
    typedef ap_fixed<32,16> threshold_t;
    typedef ap_fixed<32,16> input_t;
    typedef ap_fixed<32,16> weight_t;
    typedef ap_fixed<32,16> score_t;
    static constexpr bool useAddTree = false;
};

namespace py = pybind11;
PYBIND11_MODULE(conifer_bridge_1784457101, m){
  py::class_<conifer::BDT<BDTConfig>>(m, "BDT", py::module_local())
      .def(py::init<const std::string &>())
      .def("decision_function", &conifer::BDT<BDTConfig>::_decision_function_double);
}