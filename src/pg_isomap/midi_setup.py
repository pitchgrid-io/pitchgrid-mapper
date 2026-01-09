"""
MIDI setup message building from controller config templates.

This module parses MIDI message templates from controller YAML configs
and generates the actual MIDI byte sequences to send to controllers.
"""

import logging
import re
from typing import Dict, List, Optional, Tuple

from .controller_config import ControllerConfig

logger = logging.getLogger(__name__)


class MIDITemplateBuilder:
    """Build MIDI messages from controller config templates."""

    def __init__(self, controller_config: ControllerConfig):
        """
        Initialize template builder.

        Args:
            controller_config: Controller configuration with templates
        """
        self.controller_config = controller_config
        # Access the raw YAML config dict for template variables
        self.config = controller_config.config

    def build_midi_message(self, template: str, **kwargs) -> List[int]:
        """
        Parse template string and build MIDI message bytes.

        Supports:
        - Simple numbers: "240 127 247"
        - Hex notation: "0xF0 0x7F 0xF7"
        - Template variables: "{x}" "{y}" "{color}"
        - Expressions: "{7-y}" "{x & 0x7F}"
        - Function calls: "MSB(x)" "keyIndex(x, y)"
        - Config references: "NRPN(x, y)"

        Args:
            template: Template string from YAML config
            **kwargs: Variables for substitution (x, y, color, etc.)

        Returns:
            List of MIDI byte values (0-255)
        """
        # Handle template variables with expressions like {7-y} or {color}
        def replace_template_var(match):
            expr = match.group(1)
            try:
                # Evaluate the expression in the current context
                result = eval(expr, {"__builtins__": {}}, kwargs)
                return str(int(result))
            except Exception as e:
                logger.warning(f"Error evaluating template expression '{expr}': {e}")
                return "0"

        # Replace {expression} patterns
        template = re.sub(r'\{([^}]+)\}', replace_template_var, template)

        # Parse the template into tokens and build message
        message = []
        i = 0
        chars = list(template)

        while i < len(chars):
            # Skip whitespace
            while i < len(chars) and chars[i].isspace():
                i += 1
            if i >= len(chars):
                break

            start = i
            if chars[i].isdigit() or (chars[i] == '0' and i + 1 < len(chars) and chars[i + 1] == 'x'):
                # Number (decimal or hex)
                while i < len(chars) and (chars[i].isdigit() or chars[i] in 'abcdefABCDEFx'):
                    i += 1
                num_str = ''.join(chars[start:i])
                if num_str.startswith('0x'):
                    message.append(int(num_str, 16))
                else:
                    message.append(int(num_str))
            else:
                # Expression (function call or config reference)
                paren_count = 0
                while i < len(chars) and (not chars[i].isspace() or paren_count > 0):
                    if chars[i] == '(':
                        paren_count += 1
                    elif chars[i] == ')':
                        paren_count -= 1
                    i += 1

                expr = ''.join(chars[start:i]).strip()

                if expr in self.config:
                    # Config reference (like MANUFACTURER_CODE, MSB, NRPN)
                    config_value = self.config[expr]
                    if isinstance(config_value, str) and config_value.startswith('0x'):
                        # Hex value
                        message.append(int(config_value, 16))
                    elif isinstance(config_value, str):
                        # Parse the config value as a template itself (macro expansion)
                        sub_message = self.build_midi_message(config_value, **kwargs)
                        message.extend(sub_message)
                    else:
                        message.append(int(config_value))
                elif expr in kwargs:
                    # Direct parameter
                    message.append(int(kwargs[expr]))
                else:
                    # Handle special function calls like NRPN(x, y)
                    if expr.startswith('NRPN(') and expr.endswith(')'):
                        try:
                            # Extract arguments from NRPN(x, y)
                            args_match = re.match(r'NRPN\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)', expr)
                            if args_match:
                                nrpn_x = int(args_match.group(1))
                                nrpn_y = int(args_match.group(2))
                                # Build NRPN message using the template
                                nrpn_template = self.config.get('NRPN', '')
                                if nrpn_template:
                                    nrpn_message = self.build_midi_message(nrpn_template, x=nrpn_x, y=nrpn_y)
                                    message.extend(nrpn_message)
                                else:
                                    logger.error("NRPN template not found in config")
                            else:
                                logger.error(f"Could not parse NRPN arguments from '{expr}'")
                        except Exception as e:
                            logger.error(f"Error processing NRPN function '{expr}': {e}")
                    else:
                        # Try to evaluate as lambda (function call like MSB(x) or keyIndex(x, y))
                        try:
                            result = self._evaluate_lambda(expr, **kwargs)
                            message.append(result)
                        except Exception as e:
                            logger.warning(f"Could not parse expression '{expr}': {e}")
                            message.append(0)

        return message

    def _evaluate_lambda(self, lambda_expr: str, **kwargs) -> int:
        """
        Evaluate lambda expressions from config.

        Handles:
        - Function calls: keyIndex(x, y), boardIndex(x, y), MSB(x)
        - Python expressions: "(x>>7) & 0x7F", "x + 14*y"
        - Config-defined functions

        Args:
            lambda_expr: Expression to evaluate
            **kwargs: Variables for substitution

        Returns:
            Integer result
        """
        try:
            # Check if this is a function call like keyIndex(x, y) or MSB(x)
            func_match = re.match(r'(\w+)\s*\(\s*(.*?)\s*\)', lambda_expr)
            if func_match:
                func_name = func_match.group(1)
                args_str = func_match.group(2)

                # Look up function definition in config
                if func_name in self.config:
                    func_def = self.config[func_name]
                    if isinstance(func_def, str):
                        # Parse arguments
                        args = []
                        if args_str.strip():
                            args = [arg.strip() for arg in args_str.split(',')]

                        logger.debug(f"Evaluating config function: {func_name}({args})")

                        # Create evaluation context with arguments
                        eval_kwargs = kwargs.copy()

                        # Map positional args to parameter names (x, y, z, w)
                        param_names = ['x', 'y', 'z', 'w']
                        for i, arg in enumerate(args):
                            if i < len(param_names):
                                # Try to evaluate the argument
                                if arg in kwargs:
                                    eval_kwargs[param_names[i]] = kwargs[arg]
                                elif arg.isdigit():
                                    eval_kwargs[param_names[i]] = int(arg)
                                else:
                                    try:
                                        eval_kwargs[param_names[i]] = int(eval(arg, {"__builtins__": {}}, kwargs))
                                    except:
                                        eval_kwargs[param_names[i]] = arg

                        # Evaluate the function definition recursively
                        result = self._evaluate_lambda(func_def, **eval_kwargs)
                        logger.debug(f"Function {func_name} returned: {result}")
                        return result

            # Extract the expression part
            expr = lambda_expr.strip()
            if expr.startswith('(') and expr.endswith(')'):
                # Remove outer parentheses if they wrap the whole expression
                paren_count = 0
                for i, char in enumerate(expr):
                    if char == '(':
                        paren_count += 1
                    elif char == ')':
                        paren_count -= 1
                        if paren_count == 0 and i == len(expr) - 1:
                            expr = expr[1:-1].strip()
                            break

            # Replace variables (only standalone words, not parts of function names)
            for key, value in kwargs.items():
                expr = re.sub(r'\b' + re.escape(key) + r'\b', str(value), expr)

            # Safe evaluation with limited scope
            allowed_names = {
                "__builtins__": {},
                "x": kwargs.get('x', 0),
                "y": kwargs.get('y', 0),
                "z": kwargs.get('z', 0),
                "w": kwargs.get('w', 0),
                "noteNumber": kwargs.get('noteNumber', 0),
                "midiChannel": kwargs.get('midiChannel', 0),
                "red": kwargs.get('red', 0),
                "green": kwargs.get('green', 0),
                "blue": kwargs.get('blue', 0),
                # Built-in helper functions
                "cumulativeIndex": self.controller_config.cumulativeIndex,
            }

            # Add config-defined functions to evaluation context
            for func_name, func_def in self.config.items():
                if isinstance(func_def, str) and func_name not in ['DeviceName', 'MIDIDeviceName']:
                    # Create a lambda that can be called during eval
                    def make_func(name, definition):
                        def func(*args, **func_kwargs):
                            arg_names = ['x', 'y', 'z', 'w']
                            eval_kwargs = func_kwargs.copy()
                            for i, arg in enumerate(args):
                                if i < len(arg_names):
                                    eval_kwargs[arg_names[i]] = arg
                            return self._evaluate_lambda(definition, **eval_kwargs)
                        return func

                    allowed_names[func_name] = make_func(func_name, func_def)

            return int(eval(expr, {"__builtins__": {}}, allowed_names))

        except Exception as e:
            logger.error(f"Error evaluating lambda '{lambda_expr}' with kwargs {kwargs}: {e}")
            return 0

    def set_pad_notes_bulk(self, pads: List[Dict]) -> Optional[List[int]]:
        """
        Build SetPadNotesBulk message for all pads.

        Args:
            pads: List of pad dicts with keys: x, y, noteNumber, midiChannel

        Returns:
            MIDI message bytes or None if template not defined
        """
        if not self.controller_config.set_pad_notes_bulk:
            return None

        template = self.controller_config.set_pad_notes_bulk

        # Handle for loops in template
        if '{#for pad in pads}' in template and '{#end}' in template:
            prefix = template.split('{#for pad in pads}')[0]
            loop_content = template.split('{#for pad in pads}')[1].split('{#end}')[0]
            suffix = template.split('{#end}')[1]

            message = self.build_midi_message(prefix)

            for pad in pads:
                # Substitute pad variables in loop content
                substituted_content = loop_content
                substituted_content = substituted_content.replace('pad.x', str(pad['x']))
                substituted_content = substituted_content.replace('pad.y', str(pad['y']))
                substituted_content = substituted_content.replace('pad.noteNumber', str(pad.get('noteNumber', 60)))
                substituted_content = substituted_content.replace('pad.midiChannel', str(pad.get('midiChannel', 0)))

                pad_message = self.build_midi_message(substituted_content, **pad)
                message.extend(pad_message)

            message.extend(self.build_midi_message(suffix))
            return message
        else:
            return self.build_midi_message(template)

    def set_pad_colors_bulk(self, pads: List[Dict]) -> Optional[List[int]]:
        """
        Build SetPadColorsBulk message for all pads.

        Args:
            pads: List of pad dicts with keys: x, y, red, green, blue, color

        Returns:
            MIDI message bytes or None if template not defined
        """
        if not self.controller_config.set_pad_colors_bulk:
            return None

        template = self.controller_config.set_pad_colors_bulk

        # Handle for loops in template
        if '{#for pad in pads}' in template and '{#end}' in template:
            prefix = template.split('{#for pad in pads}')[0]
            loop_content = template.split('{#for pad in pads}')[1].split('{#end}')[0]
            suffix = template.split('{#end}')[1]

            message = self.build_midi_message(prefix)

            for pad in pads:
                # Substitute pad variables in loop content
                substituted_content = loop_content
                substituted_content = substituted_content.replace('pad.x', str(pad['x']))
                substituted_content = substituted_content.replace('pad.y', str(pad['y']))
                substituted_content = substituted_content.replace('pad.red', str(pad.get('red', 0)//2))
                substituted_content = substituted_content.replace('pad.green', str(pad.get('green', 0)//2))
                substituted_content = substituted_content.replace('pad.blue', str(pad.get('blue', 0)//2))
                pad_message = self.build_midi_message(substituted_content, **pad)
                message.extend(pad_message)

            message.extend(self.build_midi_message(suffix))
            return message
        else:
            return self.build_midi_message(template)

    def set_pad_color(self, x: int, y: int, red: int, green: int, blue: int, color: int = 0) -> Optional[List[int]]:
        """
        Build individual SetPadColor message for one pad.

        Args:
            x: Logical X coordinate
            y: Logical Y coordinate
            red: Red component (0-255)
            green: Green component (0-255)
            blue: Blue component (0-255)
            color: Color enum (for controllers like LinnStrument)

        Returns:
            MIDI message bytes or None if template not defined
        """
        if not self.controller_config.set_pad_color:
            return None

        template = self.controller_config.set_pad_color
        return self.build_midi_message(template, x=x, y=y, red=red, green=green, blue=blue, color=color)
