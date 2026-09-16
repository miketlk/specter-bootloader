function(specter_encode_mock_version version output_variable)
    if(NOT version MATCHES
       "^([0-9]|[1-3][0-9]|4[01])\\.([0-9]|[1-9][0-9]|[1-9][0-9][0-9])\\.([0-9]|[1-9][0-9]|[1-9][0-9][0-9])$")
        message(FATAL_ERROR
            "SPECTER_MOCK_VERSION must be major.minor.patch within tag10 bounds")
    endif()

    string(REPLACE "." ";" version_parts "${version}")
    list(GET version_parts 0 version_major)
    list(GET version_parts 1 version_minor)
    list(GET version_parts 2 version_patch)

    if(version_major LESS 10)
        set(version_major "0${version_major}")
    endif()
    foreach(component version_minor version_patch)
        if(${component} LESS 10)
            set(${component} "00${${component}}")
        elseif(${component} LESS 100)
            set(${component} "0${${component}}")
        endif()
    endforeach()

    set(${output_variable}
        "${version_major}${version_minor}${version_patch}99" PARENT_SCOPE)
endfunction()
