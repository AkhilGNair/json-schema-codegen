import json

from json_codegen.ast import javascript as ast
from json_codegen.core import SchemaParser, BaseGenerator
from json_codegen.js_utils import get_type_annotation


class JavaScriptFlowGenerator(SchemaParser, BaseGenerator):
    def generate(self):
        # Generates definitions first
        self._body = []

        for definition in self.get_klass_definitions():
            self._body.append(self.klass(definition))

        # Generate root definition
        root_definition = self.get_root_definition()

        if "title" in self.schema:
            self._body.append(self.klass(root_definition))

        # Add leading comments
        if len(self._body):
            self._body[0]["leadingComments"] = [ast.CommentLine("@flow")]

        return self

    def klass(self, definition):
        # Build class property Flow definition
        body = []
        required = definition.get("required", ())
        properties = definition.get("properties", {})

        for key in sorted(properties.keys()):
            # Add property type definition
            property_ = properties[key]
            is_required = key in required
            has_default = "default" in property_

            property_annotation = get_type_annotation(
                self.definitions, property_, required=(is_required or has_default)
            )
            property_def = ast.ClassProperty(
                key=ast.Identifier(key), typeAnnotation=ast.TypeAnnotation(property_annotation)
            )

            body.append(property_def)

        # Add class constructor
        if len(properties):
            body.append(self.klass_constructor(properties))

        # Return class definition
        return ast.ExportNamedDeclaration(
            declaration=ast.ClassDeclaration(
                id_=ast.Identifier(definition["title"]), body=ast.ClassBody(body=body)
            )
        )

    def _get_default_for_array(self, name, definition):
        # Test expression
        test = ast.CallExpression(
            callee=ast.MemberExpression(ast.Identifier("Array"), ast.Identifier("isArray")),
            arguments=[ast.MemberExpression(ast.Identifier("data"), ast.Identifier(name))],
        )

        # Consequent expression
        items = definition.get("items", [])

        consequent = ast.MemberExpression(ast.Identifier("data"), property_=ast.Identifier(name))

        if len(items) == 1 and "$ref" in items[0]:
            ref_key = items[0]["$ref"]
            ref = self.definitions[ref_key]

            if not self.definition_is_primitive_alias(ref):
                consequent = ast.CallExpression(
                    callee=ast.MemberExpression(consequent, ast.Identifier("map")),
                    arguments=[
                        ast.ArrowFunctionExpression(
                            params=[ast.Identifier("v")],
                            body=ast.CallExpression(
                                ast.Identifier(ref["title"]), [ast.Identifier("v")]
                            ),
                        )
                    ],
                )

        # Alternate expression
        alternate = ast.ArrayExpression(
            elements=[ast.NumericLiteral(v) for v in definition["default"]]
        )

        # Return condition
        return ast.ConditionalExpression(test, consequent, alternate)

    def _get_default_for_integer(self, name, definition):
        test = ast.CallExpression(
            callee=ast.MemberExpression(ast.Identifier("Number"), ast.Identifier("isInteger")),
            arguments=[ast.MemberExpression(ast.Identifier("data"), ast.Identifier(name))],
        )

        consequent = ast.MemberExpression(ast.Identifier("data"), ast.Identifier(name))
        alternate = ast.NumericLiteral(definition["default"])

        return ast.ConditionalExpression(test, consequent, alternate)

    def _get_default_for_boolean(self, name, definition):
        test = ast.BinaryExpression(
            left=ast.UnaryExpression(
                operator="typeof",
                argument=ast.MemberExpression(ast.Identifier("data"), ast.Identifier(name)),
            ),
            right=ast.StringLiteral("boolean"),
        )

        consequent = ast.MemberExpression(ast.Identifier("data"), ast.Identifier(name))
        alternate = ast.BooleanLiteral(definition["default"])

        return ast.ConditionalExpression(test, consequent, alternate)

    def _get_default_for_string(self, name, definition):
        test = ast.BinaryExpression(
            left=ast.UnaryExpression(
                operator="typeof",
                argument=ast.MemberExpression(ast.Identifier("data"), ast.Identifier(name)),
            ),
            right=ast.StringLiteral("string"),
        )

        consequent = ast.MemberExpression(ast.Identifier("data"), ast.Identifier(name))
        alternate = ast.StringLiteral(definition["default"])

        return ast.ConditionalExpression(test, consequent, alternate)

    def _get_default_for_object(self, name, definition):
        def ast_from_dict(d):
            properties = []

            for k, v in d.items():
                key = ast.Identifier(k)
                value = ast.NumericLiteral(v)

                properties.append(ast.ObjectProperty(key, value))

            return ast.ObjectExpression(properties)

        test = ast.LogicalExpression(
            left=ast.BinaryExpression(
                left=ast.MemberExpression(ast.Identifier("data"), ast.Identifier(name)),
                right=ast.NullLiteral(),
                operator="!==",
            ),
            right=ast.BinaryExpression(
                left=ast.UnaryExpression(
                    operator="typeof",
                    argument=ast.MemberExpression(ast.Identifier("data"), ast.Identifier(name)),
                ),
                right=ast.StringLiteral("object"),
            ),
        )

        consequent = ast.MemberExpression(ast.Identifier("data"), ast.Identifier(name))
        alternate = ast_from_dict(definition["default"])

        return ast.ConditionalExpression(test, consequent, alternate)

    def _get_default_for_property(self, name, definition):
        type_ = definition.get("type")

        if type_ == "integer":
            return self._get_default_for_integer(name, definition)
        elif type_ == "array":
            return self._get_default_for_array(name, definition)
        elif type_ == "boolean":
            return self._get_default_for_boolean(name, definition)
        elif type_ == "string":
            return self._get_default_for_string(name, definition)
        elif type_ == "object":
            return self._get_default_for_object(name, definition)
        else:
            raise NotImplementedError("{}: {} => {}".format(self, name, definition))

    def _get_reducer_for_property(self, key, property_):
        # Object.entries()
        object_entries = ast.CallExpression(
            ast.MemberExpression(ast.Identifier("Object"), ast.Identifier("entries")),
            [ast.MemberExpression(ast.Identifier("data"), ast.Identifier(key))],
        )

        # reduce()
        # ...deconstruct `entry`...
        deconstruct_entry = ast.VariableDeclaration(
            [
                ast.VariableDeclarator(
                    id_=ast.ArrayPattern(
                        [
                            ast.Identifier(
                                "key",
                                type_annotation=ast.TypeAnnotation(ast.StringTypeAnnotation()),
                            ),
                            ast.Identifier(
                                "value",
                                type_annotation=ast.TypeAnnotation(
                                    ast.GenericTypeAnnotation(ast.Identifier("Object"))
                                ),
                            ),
                        ]
                    ),
                    init=ast.TypeCastExpression(
                        ast.Identifier("entry"), ast.TypeAnnotation(ast.AnyTypeAnnotation())
                    ),
                )
            ]
        )

        # ...assign newValue...
        ref_title = self.definitions[property_["$ref"]]["title"]

        new_value = ast.VariableDeclaration(
            [
                ast.VariableDeclarator(
                    ast.Identifier("newValue"),
                    ast.NewExpression(
                        ast.Identifier(ref_title), arguments=[ast.Identifier("value")]
                    ),
                )
            ]
        )

        # ...update acc...
        update_acc = ast.ExpressionStatement(
            ast.AssignmentExpression(
                left=ast.MemberExpression(
                    ast.Identifier("acc"), ast.Identifier("key"), computed=True
                ),
                right=ast.Identifier("newValue"),
            )
        )

        # ...return acc...
        return_acc = ast.ReturnStatement(ast.Identifier("acc"))

        # ...bound together
        reduce = ast.CallExpression(
            callee=ast.MemberExpression(object_entries, ast.Identifier("reduce")),
            arguments=[
                ast.ArrowFunctionExpression(
                    params=[ast.Identifier("acc"), ast.Identifier("entry")],
                    body=ast.BlockStatement(
                        [deconstruct_entry, new_value, update_acc, return_acc]
                    ),
                ),
                ast.ObjectExpression(),
            ],
        )

        # Return reduce
        return reduce

    def _get_member_right_assignment(self, key, property_):
        additional_properties = property_.get("additionalProperties")

        if additional_properties is not None:
            if "$ref" not in additional_properties:
                raise NotImplementedError(
                    "Scalar types for additionalProperties not supported yet"
                )

            return self._get_reducer_for_property(key, additional_properties)

        if "default" in property_:
            return self._get_default_for_property(key, property_)
        else:
            return ast.MemberExpression(ast.Identifier("data"), ast.Identifier(key))

    def klass_constructor(self, properties):
        # Build constructor body
        body = []

        for key in sorted(properties.keys()):
            property_ = properties[key]

            # Left assignment
            assign_left = ast.MemberExpression(ast.ThisExpression(), ast.Identifier(key))

            # Right assignment
            assign_right = self._get_member_right_assignment(key, property_)

            # Add property assignment
            klass_property = ast.ExpressionStatement(
                ast.AssignmentExpression(assign_left, assign_right)
            )

            body.append(klass_property)

        # Build constructor parameters
        param_type = ast.TypeAnnotation(ast.GenericTypeAnnotation(id_=ast.Identifier("Object")))
        param = ast.AssignmentPattern(
            left=ast.Identifier("data", type_annotation=param_type), right=ast.ObjectExpression()
        )

        params = [param]

        # Return constructor method
        block = ast.BlockStatement(body=body)

        return ast.ClassMethod(
            key=ast.Identifier("constructor"), kind="constructor", params=params, body=block
        )

    def as_ast(self):
        comments = [ast.CommentLine("@flow")] if len(self._body) else []
        file_ = ast.File(program=ast.Program(body=self._body), comments=comments)

        return file_

    def as_code(self):
        return json.dumps(self.as_ast(), indent=2)
